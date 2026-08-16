#!/bin/bash

# Setup script for private PyPI server

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Setting up Private PyPI Server for spyMonk-DB${NC}"
echo ""

# Create necessary directories
echo -e "${BLUE}[1/4]${NC} Creating directories..."
mkdir -p packages
echo -e "${GREEN}✓${NC} Directories created"
echo ""

# Generate password file
echo -e "${BLUE}[2/4]${NC} Setting up authentication..."
echo -e "${YELLOW}Enter username for PyPI server (e.g., spymonk):${NC}"
read -r USERNAME

echo -e "${YELLOW}Enter password:${NC}"
read -rs PASSWORD

# Create htpasswd file
pip install passlib
python3 << EOF
from passlib.apache import HtpasswdFile
ht = HtpasswdFile("htpasswd", new=True)
ht.set_password("${USERNAME}", "${PASSWORD}")
ht.save()
print("✓ Password file created")
EOF

echo -e "${GREEN}✓${NC} Authentication configured"
echo ""

# Start the server
echo -e "${BLUE}[3/4]${NC} Starting PyPI server with Docker..."
docker-compose up -d

echo -e "${GREEN}✓${NC} Server started"
echo ""

# Wait for server to be ready
echo -e "${BLUE}[4/4]${NC} Waiting for server to be ready..."
sleep 3

if curl -f http://localhost:8080 > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Server is running"
else
    echo -e "${YELLOW}⚠${NC} Server might still be starting..."
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Private PyPI Server Setup Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${BLUE}Server URL:${NC} http://localhost:8080"
echo -e "${BLUE}Username:${NC} ${USERNAME}"
echo -e "${BLUE}Password:${NC} <hidden>"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Configure pip: ./configure-pip.sh"
echo "  2. Upload package: ./upload-package.sh"
echo ""
echo -e "${YELLOW}To stop server:${NC} docker-compose down"
echo -e "${YELLOW}To view logs:${NC} docker-compose logs -f"
echo ""
