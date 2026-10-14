import { BadGatewayException, Injectable } from '@nestjs/common';

import { env } from '../config.js';

export interface Stock {
  product_id: number;
  available: number;
  source: 'downstream';
}

@Injectable()
export class DownstreamService {
  private readonly baseUrl = env('DOWNSTREAM_URL', 'http://127.0.0.1:9000').replace(/\/+$/, '');

  /**
   * Test 6: built-in fetch (undici). Its global dispatcher is one shared keep-alive Agent with
   * no per-origin connection limit, which is the pool the spec asks for.
   */
  async inventory(id: number): Promise<Stock> {
    let available: number;
    try {
      const res = await fetch(`${this.baseUrl}/inventory/${id}`);
      if (!res.ok) throw new Error(`downstream returned ${res.status}`);
      ({ available } = (await res.json()) as { available: number });
    } catch (e) {
      console.error('error: downstream call failed:', e);
      throw new BadGatewayException();
    }
    return { product_id: id, available, source: 'downstream' };
  }
}
