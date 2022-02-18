FROM algorand/stable:latest

WORKDIR /root/node

COPY . devnet
# use custom algod (with working devmode)
# COPY algod algod
COPY run.sh run.sh
RUN chmod u+x run.sh
