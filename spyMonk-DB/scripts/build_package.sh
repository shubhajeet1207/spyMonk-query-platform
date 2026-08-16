#!/bin/bash

# Script to build spyMonk-DB wheel package

set -e  # Exit on error

echo "🔧 Building spyMonk-DB Enterprise Package..."
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [ ! -f "setup.py" ]; then
    echo "❌ Error: setup.py not found. Please run this script from the spyMonk-DB root directory."
    exit 1
fi

# Clean previous builds
echo -e "${BLUE}[1/5]${NC} Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info
echo -e "${GREEN}✓${NC} Cleaned"
echo ""

# Install build dependencies
echo -e "${BLUE}[2/5]${NC} Installing build dependencies..."
pip3 install --upgrade pip setuptools wheel build twine
echo -e "${GREEN}✓${NC} Dependencies installed"
echo ""

# Build the package
echo -e "${BLUE}[3/5]${NC} Building wheel package..."
python3 -m build --wheel
echo -e "${GREEN}✓${NC} Wheel built"
echo ""

# Build source distribution (optional)
echo -e "${BLUE}[4/5]${NC} Building source distribution..."
python3 -m build --sdist
echo -e "${GREEN}✓${NC} Source distribution built"
echo ""

# Verify the package
echo -e "${BLUE}[5/5]${NC} Verifying package..."
twine check dist/*
echo -e "${GREEN}✓${NC} Package verified"
echo ""

# List the built packages
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Package built successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "📦 Built packages:"
ls -lh dist/
echo ""

# Show installation command
echo -e "${YELLOW}To install locally:${NC}"
echo "  pip3 install dist/spymonk_db-0.1.0-py3-none-any.whl"
echo ""
echo -e "${YELLOW}To upload to private PyPI:${NC}"
echo "  twine upload --repository-url http://your-pypi-server.com dist/*"
echo ""
