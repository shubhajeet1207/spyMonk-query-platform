# ⚡ Quick Command Reference

## 📦 Package spyMonk-DB

```bash
# One-command build
cd spyMonk-DB && ./build_package.sh
```

## 🗄️ Set Up Private PyPI

```bash
# Start local PyPI server
cd spyMonk-DB/private-pypi
./setup-pypi.sh

# Upload package
./upload-package.sh

# Configure pip on your machine
./configure-pip.sh
```

## 📥 Install in Warehouse Backend

```bash
# With private PyPI configured
cd spyMonk-warehouse/backend
pip install spymonk-db-enterprise==0.1.0

# Or with explicit URL
pip install --extra-index-url http://spymonk:password@localhost:8080/simple/ spymonk-db-enterprise
```

## 🚀 Deploy Everything

```bash
# 1. Build and upload package
cd spyMonk-DB
./build_package.sh
cd private-pypi && ./upload-package.sh

# 2. Deploy PyPI server to Railway
cd private-pypi
railway init && railway up

# 3. Deploy DB cluster
cd ../
railway init && railway up

# 4. Deploy backend
cd ../spyMonk-warehouse/backend
railway init && railway up

# 5. Deploy frontend to Cloudflare
cd ..
npm run build
wrangler pages deploy dist
```

## 🔍 Verify Installation

```bash
# Test package
python -c "from spymonk_enterprise.client import SpyMonkClient; print('✅ Works!')"

# Check version
pip show spymonk-db-enterprise

# List packages on PyPI server
curl http://localhost:8080/simple/
```

## 🔄 Update Package Version

```bash
# 1. Edit pyproject.toml version
# 2. Rebuild
cd spyMonk-DB && ./build_package.sh

# 3. Re-upload
cd private-pypi && ./upload-package.sh

# 4. Update warehouse requirements.txt
echo "spymonk-db-enterprise==0.2.0" >> ../spyMonk-warehouse/backend/requirements.txt
```

## 🐛 Troubleshooting

```bash
# Clear pip cache
pip cache purge

# Force reinstall
pip install --force-reinstall --no-cache-dir spymonk-db-enterprise

# Check pip config
pip config list

# Test PyPI server
curl http://localhost:8080

# View Docker logs
cd private-pypi && docker-compose logs -f
```
