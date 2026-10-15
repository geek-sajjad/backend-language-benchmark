using System.Text.Json;
using Microsoft.AspNetCore.Diagnostics;

namespace Bench.Infrastructure;

// Error bodies from docs/02-test-spec.md; conformance checks only the status code.
public sealed class ApiErrors(ILogger<ApiErrors> log) : IExceptionHandler
{
    public static IResult NotFound() => Results.Json(new { error = "not_found" }, statusCode: 404);
    public static IResult Forbidden() => Results.Json(new { error = "forbidden" }, statusCode: 403);
    public static IResult BadGateway() => Results.Json(new { error = "downstream_failed" }, statusCode: 502);

    public static IResult ValidationFailed(object details) =>
        Results.Json(new { error = "validation_failed", details }, statusCode: 422);

    // Binding failures (ThrowOnBadRequest): an unreadable JSON body is a validation error (422);
    // a bad path or query parameter stays a 400. Everything else falls through to the default 500.
    public async ValueTask<bool> TryHandleAsync(HttpContext http, Exception exception, CancellationToken ct)
    {
        if (exception is not BadHttpRequestException bad)
        {
            return false;
        }
        if (bad.InnerException is JsonException json)
        {
            await ValidationFailed(json.Message).ExecuteAsync(http);
        }
        else
        {
            log.LogDebug(bad, "bad request");
            await Results.Json(new { error = "bad_request", details = bad.Message }, statusCode: bad.StatusCode).ExecuteAsync(http);
        }
        return true;
    }

    // The AddValidation() filter answers 400 with a ValidationProblem; the spec wants 422.
    public static void ValidationAs422(ProblemDetailsContext ctx)
    {
        if (ctx.ProblemDetails is HttpValidationProblemDetails)
        {
            ctx.HttpContext.Response.StatusCode = StatusCodes.Status422UnprocessableEntity;
            ctx.ProblemDetails.Status = StatusCodes.Status422UnprocessableEntity;
            ctx.ProblemDetails.Type = "https://tools.ietf.org/html/rfc9110#section-15.5.21";
            ctx.ProblemDetails.Extensions["error"] = "validation_failed";
        }
    }
}
