# Production Deployment Guide

## Changes Made for Production

### 1. **Debug Mode Disabled**
   - Changed `debug=True` to `debug=False` in app startup
   - Disables Flask development features that expose security vulnerabilities

### 2. **Updated Requirements**
   - Added `gunicorn[gevent]` - production-grade WSGI server
   - Added `python-dotenv` - for environment variable management

### 3. **WSGI Entry Point** (`wsgi.py`)
   - Created standard WSGI application entry point
   - Use with `gunicorn` for production deployment

### 4. **Environment Configuration** (`.env.production`)
   - Set `FLASK_ENV=production`
   - Set `FLASK_DEBUG=0` (disabled)
   - Define secure secret key (CHANGE THIS BEFORE DEPLOYING)

### 5. **Production Startup Script** (`run_production.bat`)
   - Uses gunicorn with multiple workers for load distribution
   - Configured for gevent compatibility with SocketIO

## Deployment Instructions

### Option 1: Using Gunicorn (Recommended)
```bash
pip install -r requirements.txt
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:5710 --workers 4 wsgi:app
```

### Option 2: Using Run Script (Windows)
```bash
run_production.bat
```

### Before Going Live

1. **Update Secret Key**
   - Replace `SECRET_KEY` in `.env.production` with a secure random key
   - Use: `python -c "import secrets; print(secrets.token_hex(32))"`

2. **Set Environment Variable**
   - `set FLASK_ENV=production`
   - Or use `.env.production` file

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Enable HTTPS**
   - Configure with reverse proxy (nginx/Apache)
   - Use SSL/TLS certificates

5. **Configure Logging**
   - Redirect logs to a file: `gunicorn ... --access-logfile access.log --error-logfile error.log`

6. **Performance Tuning**
   - Adjust `--workers` based on CPU cores: `(2 × CPU_cores) + 1`
   - Current config: 4 workers

## Additional Security Recommendations

- [ ] Set up firewall rules
- [ ] Use environment variables for sensitive data
- [ ] Enable CORS only for trusted origins
- [ ] Implement rate limiting
- [ ] Use HTTPS/TLS
- [ ] Regular security updates
- [ ] Monitoring and alerting setup

## Differences from Development

| Feature | Development | Production |
|---------|-------------|-----------|
| Debug Mode | Enabled | Disabled |
| Auto-reload | Yes | No |
| Error Pages | Detailed | Generic |
| Server | Flask dev server | Gunicorn (WSGI) |
| Workers | 1 | 4+ |
| Performance | Optimized for dev | Optimized for scale |
