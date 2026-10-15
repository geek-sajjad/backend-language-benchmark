# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/dotnet/sdk:10.0.402-noble@sha256:0d99ebf1ccad15037b5c98c9fe8979d6b8dbac23812feae2277a0b277b5c5ffa AS build
WORKDIR /src
COPY Bench.csproj packages.lock.json ./
RUN --mount=type=cache,target=/root/.nuget/packages dotnet restore --locked-mode
COPY Program.cs appsettings.json ./
COPY Data/ Data/
COPY Endpoints/ Endpoints/
COPY Infrastructure/ Infrastructure/
RUN --mount=type=cache,target=/root/.nuget/packages \
    dotnet publish -c Release --no-restore -o /app

FROM mcr.microsoft.com/dotnet/aspnet:10.0.13-noble@sha256:d5633778c754e89a386882eddf8dbee6942447a8a4b8267c19cf1e1158d0b0ac
WORKDIR /app
COPY --from=build /app ./
# The image already listens on 8080 (ASPNETCORE_HTTP_PORTS) and defines the non-root "app" user.
USER $APP_UID
EXPOSE 8080
ENTRYPOINT ["dotnet", "bench-dotnet.dll"]
