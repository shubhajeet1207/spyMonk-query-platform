#!/bin/bash

# Configure pip to use private PyPI server

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Configuring pip for Private PyPI Server${NC}"
echo ""

# Get server details
echo -e "${YELLOW}Enter PyPI server URL (default: http://localhost:8080):${NC}"
read -r SERVER_URL
SERVER_URL=${SERVER_URL:-http://localhost:8080}

echo -e "${YELLOW}Enter username:${NC}"
read -r USERNAME

echo -e "${YELLOW}Enter password:${NC}"
read -rs PASSWORD
echo ""

# Create pip config
echo -e "${BLUE}[1/2]${NC} Creating pip configuration..."

mkdir -p ~/.pip

cat > ~/.pip/pip.conf << EOF
[global]
# Use private PyPI server as additional index
extra-index-url = http://${USERNAME}:${PASSWORD}@${SERVER_URL#http://}/simple/
trusted-host = ${SERVER_URL#http://}
EOF

echo -e "${GREEN}✓${NC} Pip configured at ~/.pip/pip.conf"
echo ""

# Create .pypirc for uploading
echo -e "${BLUE}[2/2]${NC} Creating .pypirc for package uploads..."

cat > ~/.pypirc << EOF
[distutils]
index-servers =
    spymonk-private

[spymonk-private]
repository: ${SERVER_URL}
username: ${USERNAME}
password: ${PASSWORD}
EOF

echo -e "${GREEN}✓${NC} Upload config created at ~/.pypirc"
echo ""

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Pip Configuration Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}You can now:${NC}"
echo "  • Install from private PyPI: pip3 install spymonk-db-enterprise"
echo "  • Upload packages: twine upload --repository spymonk-private dist/*"
echo ""
