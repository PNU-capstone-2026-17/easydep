FROM node:20

WORKDIR /opt/puml2code-bce
COPY package.json package-lock.json ./
COPY bin ./bin
COPY src ./src
RUN npm ci --omit=dev

ENTRYPOINT ["node", "/opt/puml2code-bce/bin/puml2code"]
