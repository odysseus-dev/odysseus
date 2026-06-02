FROM fedora:41

ENV PIP_BREAK_SYSTEM_PACKAGES=1

# System deps mirror the production Dockerfile; gosu is installed from upstream
# because Fedora does not ship it in the base repos.
RUN dnf install -y \
    python3.12 \
    python3.12-devel \
    gcc \
    gcc-c++ \
    make \
    cmake \
    git \
    nodejs \
    npm \
    tmux \
    openssh-clients \
    && dnf clean all \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && python3.12 -m ensurepip --upgrade

RUN curl -fsSL -o /usr/local/bin/gosu \
    "https://github.com/tianon/gosu/releases/download/1.17/gosu-amd64" \
    && chmod +x /usr/local/bin/gosu

WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data logs services/cache/search

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
