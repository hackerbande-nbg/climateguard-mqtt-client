<<<<<<< HEAD
# ClimatGuard MQTT Client

MQTT client service for ClimateGuard monitoring system.

## Deployment

The service is automatically deployed to the production server using GitHub Actions.

### Automated Deployment (Recommended)

1. Navigate to the repository on GitHub
2. Go to **Actions** tab
3. Select **prod deployment** workflow
4. Click **Run workflow** button
5. Confirm by clicking **Run workflow**

This will automatically:

- Pull the latest code
- Build the Docker image
- Restart the service with the new version

### Manual Deployment

If you need to deploy manually on the server:

```bash
# Navigate to project directory
cd /home/andi/git/climateguard-mqtt-client

# Pull latest changes
git pull

# Restart the service
docker compose -p mqtt down
docker compose -p mqtt up -d --build --force-recreate
```

## Restarting the Service

### Quick Restart (without rebuild)

```bash
docker compose -p mqtt restart
```

### Full Restart (with rebuild)

```bash
docker compose -p mqtt down
docker compose -p mqtt up -d --build --force-recreate
```

### View Logs

```bash
# Follow logs
docker compose -p mqtt logs -f

# View last 100 lines
docker compose -p mqtt logs --tail=100
```

## Environment Configuration

The production environment variables are stored in GitHub Secrets as `QUANTUM_MQTT_ENV_PROD` and are automatically deployed during the GitHub Actions workflow.

For local development, create a `.env` file in the project root with the necessary configuration.
=======
# quantum-mqtt-client
quantum telemetry mqtt client pulling data from TTN

# Payload Layout and Versioning Concept

The first payload byte uniquely identifies the payload format version. This approach allows for flexible evolution of the payload structure while maintaining backward compatibility and clear parsing rules for consumers.

[View the JSON payload layout mapping for v1](./config/payload_layout.json)

>>>>>>> 882dbba92e7e0463529825f75eb27b2cf8b5b021
