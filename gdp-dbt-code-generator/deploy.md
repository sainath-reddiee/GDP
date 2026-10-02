# Deploying to Ubuntu VM

This guide walk you through setting up an Ubuntu VM and deploying the AI-DBT Code Generator using Docker.

## Prerequisites
- An Ubuntu VM (20.04 or 22.04 recommended)
- SSH access
- Admin (sudo) privileges

## Step 1: Install Docker & Docker Compose

Run the following commands on your VM to install the necessary tools:

```bash
# Update package list
sudo apt-get update

# Install Docker
sudo apt-get install -y docker.io

# Install Docker Compose
sudo apt-get install -y docker-compose

# Start and enable Docker
sudo systemctl start docker
sudo systemctl enable docker

# Add your user to the docker group (optional, avoids using sudo for docker commands)
sudo usermod -aG docker $USER
# NOTE: Logout and login again for this to take effect
```

## Step 2: Clone the Repository & Prepare Environment

```bash
# Clone your code (replace with your actual repo link)
git clone <your-repo-url>
cd ai-dbt-code-generator

# Create the .env file from the template
cp .env.example .env

# Edit the .env file with your actual secrets (Snowflake credentials, Cortex model, etc.)
nano .env
```

## Step 3: Deploy with Docker Compose

```bash
# Build and start the containers in detached mode
sudo docker-compose up --build -d
```

## Step 4: Access the Application

The application will be available on port **80**. You can access it via your VM's public IP:

`http://<your-vm-public-ip>`

### Ports Used:
- **80**: Main application (Nginx serving Frontend + Proxying Backend)
- **443**: HTTPS (Optional, needs SSL setup)

## Troubleshooting

- **Check logs**: `sudo docker-compose logs -f`
- **Restart**: `sudo docker-compose restart`
- **Stop**: `sudo docker-compose down`

---
> [!TIP]
> Ensure your VM's security groups (Firewall) allow inbound traffic on Port 80.
