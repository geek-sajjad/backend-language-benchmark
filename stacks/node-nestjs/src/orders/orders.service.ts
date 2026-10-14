import { Injectable, UnprocessableEntityException } from '@nestjs/common';
import { InjectDataSource, InjectRepository } from '@nestjs/typeorm';
import { DataSource, In, QueryFailedError, Repository } from 'typeorm';

import { OrderItem } from '../entities/order-item.entity.js';
import { Order } from '../entities/order.entity.js';
import { Product } from '../entities/product.entity.js';
import type { OrderLineDto } from './create-order.dto.js';

export interface OrderView {
  id: number;
  status: string;
  total_cents: number;
  created_at: Date;
  items: Array<{ product_id: number; name: string; unit_price_cents: number; quantity: number }>;
}

export interface CustomerOrders {
  customer_id: number;
  orders: OrderView[];
}

const FOREIGN_KEY_VIOLATION = '23503';

@Injectable()
export class OrdersService {
  constructor(
    @InjectDataSource() private readonly db: DataSource,
    @InjectRepository(Order) private readonly orders: Repository<Order>,
    @InjectRepository(OrderItem) private readonly items: Repository<OrderItem>,
  ) {}

  /** Test 4: 2 queries: the 10 latest orders, then all their items with product names (`IN`). */
  async customerOrders(customerId: number): Promise<CustomerOrders> {
    const orders = await this.orders.find({
      where: { customer_id: customerId },
      order: { created_at: 'DESC', id: 'DESC' },
      take: 10,
    });
    const itemsByOrder = new Map<number, OrderView['items']>();
    if (orders.length > 0) {
      const items = await this.items.find({
        select: {
          id: true,
          order_id: true,
          product_id: true,
          quantity: true,
          unit_price_cents: true,
          product: { id: true, name: true },
        },
        relations: { product: true },
        where: { order_id: In(orders.map((o) => o.id)) },
        order: { id: 'ASC' },
      });
      for (const i of items) {
        let list = itemsByOrder.get(i.order_id);
        if (!list) itemsByOrder.set(i.order_id, (list = []));
        list.push({
          product_id: i.product_id,
          name: i.product.name,
          unit_price_cents: i.unit_price_cents,
          quantity: i.quantity,
        });
      }
    }
    return {
      customer_id: customerId,
      orders: orders.map((o) => ({
        id: o.id,
        status: o.status,
        total_cents: o.total_cents,
        created_at: o.created_at,
        items: itemsByOrder.get(o.id) ?? [],
      })),
    };
  }

  /**
   * Test 5: one transaction: read prices, insert the order and its items, decrement stock atomically.
   * Items are handled in ascending product_id order so concurrent orders lock rows in the same order.
   */
  async createOrder(customerId: number, lines: OrderLineDto[]): Promise<OrderView> {
    lines = lines.toSorted((a, b) => a.product_id - b.product_id);
    try {
      return await this.db.transaction(async (tx) => {
        const products = await tx.find(Product, {
          select: { id: true, name: true, price_cents: true },
          where: { id: In(lines.map((l) => l.product_id)) },
          order: { id: 'ASC' },
        });
        if (products.length !== lines.length) {
          throw new UnprocessableEntityException({ items: 'unknown product_id' });
        }
        const total = products.reduce((sum, p, i) => sum + p.price_cents * lines[i].quantity, 0);

        // created_at comes from the app clock: a JS Date holds milliseconds, so a DB-side now()
        // (microseconds) would not round-trip into the response exactly.
        const order = tx.create(Order, {
          customer_id: customerId,
          status: 'pending',
          total_cents: total,
          created_at: new Date(),
        });
        await tx.insert(Order, order);
        // TypeORM merges generated bigint ids back as strings, even with parseInt8.
        order.id = Number(order.id);

        await tx.insert(
          OrderItem,
          products.map((p, i) => ({
            order_id: order.id,
            product_id: p.id,
            quantity: lines[i].quantity,
            unit_price_cents: p.price_cents,
          })),
        );

        for (const line of lines) {
          await tx.decrement(Product, { id: line.product_id }, 'stock', line.quantity);
        }

        return {
          id: order.id,
          status: order.status,
          total_cents: total,
          created_at: order.created_at,
          items: products.map((p, i) => ({
            product_id: p.id,
            name: p.name,
            unit_price_cents: p.price_cents,
            quantity: lines[i].quantity,
          })),
        };
      });
    } catch (e) {
      if (e instanceof QueryFailedError && (e.driverError as { code?: string }).code === FOREIGN_KEY_VIOLATION) {
        throw new UnprocessableEntityException({ customer_id: 'unknown customer' });
      }
      throw e;
    }
  }
}
