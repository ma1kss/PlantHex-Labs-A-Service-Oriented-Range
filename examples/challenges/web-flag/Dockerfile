FROM python:3.11-slim
RUN useradd -m -u 1000 player && mkdir /srv/www \
    && echo 'FLAG{single_container_2026}' > /srv/www/flag.txt
USER player
WORKDIR /srv/www
EXPOSE 8080
CMD ["python3", "-m", "http.server", "8080"]
