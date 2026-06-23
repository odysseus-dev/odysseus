package com.example;

/**
 * Minimal sanity-check app for the Java dev container.
 * Run it from inside the container:
 *   docker compose -f docker-compose.dev.yml exec java bash
 *   cd demo && mvn -q compile exec:java
 */
public class App {
    public static void main(String[] args) {
        System.out.println("Hello from the Odysseus Java dev container 👋");
        System.out.println("Java runtime: " + System.getProperty("java.version"));
        System.out.println("Next: add Kafka/Postgres/Redis/Mongo client deps to pom.xml and connect to the dev services.");
    }
}
