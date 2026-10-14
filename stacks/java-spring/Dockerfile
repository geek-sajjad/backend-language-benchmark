# syntax=docker/dockerfile:1
FROM maven:3.10.0-eclipse-temurin-25-noble@sha256:a291659c5ffddf142e5174df5bfc3f3dcb48706e44868f341ef7914b39a254c9 AS build
WORKDIR /build
COPY pom.xml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q package -DskipTests
# Spring Boot's recommended container layout: the jar unpacked into app.jar + lib/.
RUN java -Djarmode=tools -jar target/app.jar extract --destination /app

FROM eclipse-temurin:25.0.4.1_1-jre-noble@sha256:d9a39a23634650173f1e2bbc176227af9728587ecf0f4b62d53e9355cd7a19ab
WORKDIR /app
COPY --from=build /app ./
USER ubuntu
EXPOSE 8080
# Heap ceiling from the container limit (Java's default is only 25% of it); everything else default.
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
