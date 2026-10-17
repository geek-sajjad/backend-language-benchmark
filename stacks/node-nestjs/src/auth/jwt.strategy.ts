import { createPublicKey } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { Injectable, UnauthorizedException } from '@nestjs/common';
import { PassportStrategy } from '@nestjs/passport';
import { ExtractJwt, Strategy } from 'passport-jwt';

import { env } from '../config.js';

/** The authenticated customer, from the token's `sub` claim. */
export interface AuthUser {
  customerId: number;
}

interface Claims {
  sub?: unknown;
  exp?: unknown;
}

@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor() {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      // Read and parsed once at startup; no JWKS fetching (docs/02-test-spec.md, Authentication).
      // passport-jwt hands this to jsonwebtoken, which re-parses a PEM on every verify but uses a
      // KeyObject as is. The typings only list string | Buffer, hence the cast.
      secretOrKey: createPublicKey(readFileSync(env('JWT_PUBLIC_KEY_FILE', '/run/secrets/jwt_public.pem'))) as unknown as Buffer,
      algorithms: ['RS256'],
      issuer: 'bench-idp',
      audience: 'bench-api',
    });
  }

  /** Signature, iss, aud and a present exp are checked by passport-jwt; exp and sub must exist. */
  validate(claims: Claims): AuthUser {
    const customerId = typeof claims.sub === 'string' ? Number(claims.sub) : NaN;
    if (typeof claims.exp !== 'number' || !Number.isSafeInteger(customerId)) {
      throw new UnauthorizedException();
    }
    return { customerId };
  }
}
