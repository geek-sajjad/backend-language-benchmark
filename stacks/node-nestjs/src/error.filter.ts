import { type ArgumentsHost, Catch, type ExceptionFilter, HttpException } from '@nestjs/common';
import type { ValidationError } from 'class-validator';
import type { FastifyReply } from 'fastify';

const ERROR_CODES: Record<number, string> = {
  400: 'bad_request',
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  422: 'validation_failed',
  502: 'downstream_failed',
};

/** Error bodies from docs/02-test-spec.md: `{"error": …}`, plus `details` on a 422. */
@Catch()
export class ErrorFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost) {
    const reply = host.switchToHttp().getResponse<FastifyReply>();
    let status = 500;
    let details: unknown;
    if (exception instanceof HttpException) {
      status = exception.getStatus();
      const response = exception.getResponse();
      // A string message becomes { message, error, statusCode }; an object is passed through as is.
      details = typeof response === 'object' && 'statusCode' in response && 'message' in response
        ? response.message
        : response;
    }
    if (status >= 500) console.error('error:', exception);
    const body: Record<string, unknown> = { error: ERROR_CODES[status] ?? 'internal' };
    if (status === 422) body.details = details;
    void reply.status(status).send(body);
  }
}


/** `{"items": {"0": {"quantity": ["quantity must not be greater than 10"]}}}` from class-validator errors. */
export function validationDetails(errors: ValidationError[]): Record<string, unknown> {
  const details: Record<string, unknown> = {};
  for (const e of errors) {
    details[e.property] = e.children?.length ? validationDetails(e.children) : Object.values(e.constraints ?? {});
  }
  return details;
}
