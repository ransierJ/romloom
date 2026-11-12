# syntax=docker/dockerfile:1

# ---- Stage 1: build the React frontend ----
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
# vite.config.js writes the build to /build/backend/static
RUN npm run build

# ---- Stage 2: build Skyscraper (ScreenScraper CLI, Qt/C++) ----
# Compiled from source; the runtime stage keeps only the binary + its data and
# the Qt *runtime* libraries, not the toolchain. bookworm matches python:3.12-slim
# so the Qt5 ABI lines up between build and runtime.
FROM debian:bookworm-slim AS skyscraper
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential qtbase5-dev qt5-qmake qtbase5-dev-tools ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 https://github.com/Gemba/skyscraper.git . \
    && qmake && make -j"$(nproc)" && make install
# -> /usr/local/bin/Skyscraper  +  /usr/local/etc/skyscraper/ (data it copies
#    into $HOME/.skyscraper on first run)

# ---- Stage 3: Python runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# Qt runtime libs Skyscraper links against (apt resolves the transitive deps);
# ca-certificates lets it reach ScreenScraper over HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libqt5gui5 libqt5network5 libqt5sql5 libqt5xml5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=skyscraper /usr/local/bin/Skyscraper /usr/local/bin/Skyscraper
COPY --from=skyscraper /usr/local/etc/skyscraper /usr/local/etc/skyscraper

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/backend/static ./static

# HOME=/config so Skyscraper's ~/.skyscraper (config + scrape cache) lands on the
# persistent volume. QT_QPA_PLATFORM=offscreen runs it without a display.
ENV ROMLOOM_ROMS_ROOT=/roms \
    ROMLOOM_OUTPUT_ROOT=/output \
    ROMLOOM_CONFIG_DIR=/config \
    ROMLOOM_PORT=8096 \
    HOME=/config \
    QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1

EXPOSE 8096
VOLUME ["/roms", "/output", "/config"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8096"]
