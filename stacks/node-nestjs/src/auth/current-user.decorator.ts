import { createParamDecorator, type ExecutionContext } from '@nestjs/common';

import type { AuthUser } from './jwt.strategy.js';

/** The `AuthUser` that the JWT guard attached to the request. */
export const CurrentUser = createParamDecorator(
  (_: unknown, ctx: ExecutionContext): AuthUser => ctx.switchToHttp().getRequest<{ user: AuthUser }>().user,
);
