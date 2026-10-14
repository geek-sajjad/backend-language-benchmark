import { Column, Entity, PrimaryColumn } from 'typeorm';

@Entity('products')
export class Product {
  @PrimaryColumn()
  id: number;

  @Column()
  sku: string;

  @Column()
  name: string;

  @Column()
  description: string;

  @Column()
  price_cents: number;

  @Column()
  stock: number;

  @Column()
  category_id: number;

  @Column({ type: 'timestamptz' })
  created_at: Date;
}
