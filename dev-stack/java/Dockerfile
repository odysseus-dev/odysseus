# Java development container — JDK 21 (Temurin) + Maven + Gradle.
# Built by docker-compose.dev.yml (service: java). A long-lived dev sandbox:
# your code is bind-mounted at /workspace, and ~/.m2 / ~/.gradle are cached in
# named volumes so dependencies survive rebuilds.
FROM eclipse-temurin:21-jdk

ARG GRADLE_VERSION=8.10.2
ARG DEBIAN_FRONTEND=noninteractive

# Maven from apt; Gradle pinned from the official distribution (apt's is old).
RUN apt-get update && apt-get install -y --no-install-recommends \
        maven \
        git \
        curl \
        unzip \
        zip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" -o /tmp/gradle.zip \
    && unzip -q /tmp/gradle.zip -d /opt \
    && ln -s "/opt/gradle-${GRADLE_VERSION}/bin/gradle" /usr/local/bin/gradle \
    && rm /tmp/gradle.zip

# Non-root dev user so files written into the bind-mounted /workspace are owned
# by your host user (uid 1000 on most Linux/macOS), not root. Pre-create the
# cache dirs owned by dev so the mounted named volumes inherit that ownership.
RUN useradd -m -u 1000 -s /bin/bash dev \
    && mkdir -p /home/dev/.m2 /home/dev/.gradle /workspace \
    && chown -R dev:dev /home/dev /workspace

USER dev
WORKDIR /workspace

# Sanity: print toolchain versions on build so failures surface early.
RUN java -version && mvn -version && gradle --version

CMD ["sleep", "infinity"]
