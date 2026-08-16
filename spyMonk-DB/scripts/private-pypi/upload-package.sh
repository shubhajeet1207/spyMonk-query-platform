#!/bin/bash

# Upload spyMonk-DB package to private PyPI server

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}Uploading spyMonk-DB to Private PyPI${NC}"
echo ""

# Check if dist directory exists
if [ ! -d "../dist" ]; then
    echo -e "${RED}❌ Error: dist/ directory not found${NC}"
    echo "   Please build the package first:"
    echo "   cd .. && ./build_package.sh"
    exit 1
fi

# Check if packages exist
if [ ! "$(ls -A ../dist/*.whl 2>/dev/null)" ]; then
    echo -e "${RED}❌ Error: No wheel packages found in dist/${NC}"
    echo "   Please build the package first:"
    echo "   cd .. && ./build_package.sh"
    exit 1
fi

echo -e "${BLUE}[1/3]${NC} Found packages:"
ls -lh ../dist/
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

# Upload using twine
echo -e "${BLUE}[2/3]${NC} Uploading packages..."

pip3 install --upgrade twine

twine upload \
    --repository-url "${SERVER_URL}" \
    --username "${USERNAME}" \
    --password "${PASSWORD}" \
    ../dist/*

echo -e "${GREEN}✓${NC} Upload complete"
echo ""

# Copy to local packages directory
echo -e "${BLUE}[3/3]${NC} Copying to local packages directory..."
cp ../dist/* packages/
echo -e "${GREEN}✓${NC} Copied"
echo ""

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Package Uploaded Successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}To install on other machines:${NC}"
echo "  1. Configure pip: ./configure-pip.sh"
echo "  2. Install: pip3 install spymonk-db-enterprise"
echo ""
echo -e "${YELLOW}Or install directly:${NC}"
echo "  pip3 install --extra-index-url ${SERVER_URL}/simple/ spymonk-db-enterprise"
echo ""
