import { Column, Entity, OneToMany, PrimaryGeneratedColumn, type Relation } from 'typeorm';

import { OrderItem } from './order-item.entity.js';

@Entity('orders')
export class Order {
  // bigserial; the DataSource sets parseInt8, so ids arrive as numbers (they stay far below 2^53).
  @PrimaryGeneratedColumn({ type: 'bigint' })
  id: number;

  @Column()
  customer_id: number;

  @Column()
  status: string;

  @Column()
  total_cents: number;

  @Column({ type: 'timestamptz' })
  created_at: Date;

  @OneToMany(() => OrderItem, (item) => item.order)
  items: Relation<OrderItem[]>;
}
