import 'reflect-metadata';
import cluster from 'node:cluster';

import { UnprocessableEntityException, ValidationPipe } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, type NestFastifyApplication } from '@nestjs/platform-fastify';
import type { RawServerDefault } from 'fastify';

import { AppModule } from './app.module.js';
import { workers } from './config.js';
import { ErrorFilter, validationDetails } from './error.filter.js';

// Node runs JavaScript on one core per process, so the documented production setup is
// node:cluster with one worker per core. The workers share :8080 through the primary.
if (cluster.isPrimary) {
  for (let i = 0; i < workers; i++) cluster.fork();
  // Fail fast: a dead worker would silently cut the stack's capacity mid-run.
  cluster.on('exit', (worker, code, signal) => {
    console.error(`error: worker ${worker.process.pid} exited (${signal ?? code}), stopping`);
    process.exit(1);
  });
  for (const sig of ['SIGTERM', 'SIGINT'] as const) {
    process.on(sig, () => {
      cluster.removeAllListeners('exit');
      for (const w of Object.values(cluster.workers ?? {})) w?.kill();
      process.exit(0);
    });
  }
} else {
  await bootstrap();
}

async function bootstrap() {
  const app = await NestFactory.create<NestFastifyApplication>(AppModule, new FastifyAdapter(), {
    logger: ['error', 'warn'],
  });
  app.useGlobalPipes(
    new ValidationPipe({
      transform: true,
      exceptionFactory: (errors) => new UnprocessableEntityException(validationDetails(errors)),
    }),
  );
  app.useGlobalFilters(new ErrorFilter());
  rejectMalformedJsonAs422(app);
  // The JWT key, the sales dataset and the DB pool are ready before Nest finishes init,
  // so the port (and /health) only opens once all three are.
  await app.listen(8080, '0.0.0.0');
}

/**
 * Fastify's own JSON parser (with its prototype-poisoning checks), registered through Nest's
 * useBodyParser like Nest's default one, except that a body that isn't valid JSON is a 422 like
 * any other invalid input. By default Nest turns it into a generic 400.
 */
function rejectMalformedJsonAs422(app: NestFastifyApplication) {
  const parseJson = app.getHttpAdapter().getInstance().getDefaultJsonParser('error', 'error');
  app.useBodyParser<RawServerDefault>('application/json', {}, (req, body, done) =>
    // Typed for string bodies, but parses Buffers too; Nest's own parser passes the Buffer as is.
    parseJson(req, body as unknown as string, (err, value) =>
      err ? done(new UnprocessableEntityException(err.message)) : done(null, value),
    ),
  );
}
