import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { MoreThan, Repository } from 'typeorm';

import { Product } from '../entities/product.entity.js';

export interface CategoryPage {
  category_id: number;
  next_after: number | null;
  items: Array<Omit<Product, 'description' | 'category_id'>>;
}

@Injectable()
export class CatalogService {
  constructor(@InjectRepository(Product) private readonly products: Repository<Product>) {}

  /** Test 2: one product by primary key, every column. */
  async getProduct(id: number): Promise<Product> {
    const product = await this.products.findOneBy({ id });
    if (!product) throw new NotFoundException();
    return product;
  }

  /** Test 3: one keyset page of 50 full entities; `description` is loaded but not returned. */
  async categoryPage(categoryId: number, after: number): Promise<CategoryPage> {
    const products = await this.products.find({
      where: { category_id: categoryId, id: MoreThan(after) },
      order: { id: 'ASC' },
      take: 50,
    });
    const items = products.map((p) => ({
      id: p.id,
      sku: p.sku,
      name: p.name,
      price_cents: p.price_cents,
      stock: p.stock,
      created_at: p.created_at,
    }));
    return { category_id: categoryId, next_after: items.at(-1)?.id ?? null, items };
  }
}
