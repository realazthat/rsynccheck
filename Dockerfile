FROM python:3.12-slim


WORKDIR /rsynccheck

# apt-get -y --no-install-recommends install git=1:2.39.2-1.1 &&
# apt-get -y upgrade &&

COPY . /rsynccheck
RUN apt-get -y update && apt-get -y --no-install-recommends install bash=5.2.15-2+b2 xxhash=0.8.1-1 && \
  apt-get -y clean && \
  apt-get -y autoremove && \
  rm -rf /var/lib/apt/lists/* && \
  useradd -m -d /home/user -s /bin/bash user && \
  pip install --no-cache-dir --upgrade pip setuptools wheel && \
  mkdir -p /home/user/.local && \
  chown -R user:user /rsynccheck /home/user/.local && \
  chmod -R a+wrX /rsynccheck


USER user
WORKDIR /rsynccheck
ENV PATH=/home/user/.local/bin:$PATH
ENV PYTHONPATH=/home/user/.local/lib/python3.12/site-packages
RUN pip install --no-cache-dir --prefix=/home/user/.local .

# This is where the user will mount their data to.
WORKDIR /data

ENTRYPOINT ["python", "-m", "rsynccheck.cli"]
CMD ["--help"]
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -m rsynccheck.cli --version || exit 1
