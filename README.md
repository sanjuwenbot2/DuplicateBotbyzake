## Deployment

<details>
  <summary><strong>Koyeb (One-Click Deploy)</strong></summary>

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&builder=dockerfile&repository=github.com/your_name/repo&branch=main&name=leech-bot&service_type=worker)

</details>

<details>
  <summary><strong>Heroku (One-Click Deploy)</strong></summary>

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/your_name/repo/tree/main)

</details>

<details>
  <summary><strong>Render (One-Click Deploy)</strong></summary>

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/your_name/repo&branch=main)

</details>
<details>
  <summary><strong>VPS/Locally</strong></summary>

### Prerequisites

Before starting, ensure you have:
- Docker installed ([Installation Guide](https://docs.docker.com/engine/install/))
- Docker Compose installed ([Installation Guide](https://docs.docker.com/compose/install/))

### Installation Steps

1. **Clone the Repository**
   ```bash
   git clone https://github.com/ImKrishana/Poster-Scraper-Bot psb
   cd psb
   ```

2. **Setup Configuration File**

   Create `config.py` file:
   ```bash
   nano config.py
   ```

   Add the required variables:
   ```python
   BOT_TOKEN = "your_bot_token"
   TELEGRAM_API = 12345678
   TELEGRAM_HASH = "your_telegram_api_hash"
   DATABASE_URL = "your_mongodb_url"
   OWNER_ID = 123456789
   UPSTREAM_REPO = "https://github.com/ImKrishana/Poster-Scraper-Bot"
   UPSTREAM_BRANCH = "main"
   ```

   Save and exit (`Ctrl + X`, then `Y`, then `Enter`).

3. **Start the Bot**
   ```bash
   docker-compose up -d
   ```

   The bot will start in detached mode (background).

4. **Verify Bot is Running**
   ```bash
   docker-compose ps
   ```

   You should see the bot container with status `Up`.

### Xtras

**View Live Logs:**
```bash
docker-compose logs -f
```

Press `Ctrl + C` to exit logs.

**Stop the Bot:**
```bash
docker-compose down
```

**Restart the Bot:**
```bash
docker-compose restart
```

**Update and Restart:**
```bash
git pull
docker-compose up -d --build
```

**Stop and Remove Everything:**
```bash
docker-compose down -v
```

</details>

<details>
  <summary><strong>Heroku CLI</strong></summary>

1. **Login to Heroku**
   ```bash
   heroku login
   ```

2. **Clone Repository**
   ```bash
   git clone https://github.com/ImKrishana/Poster-Scraper-Bot psb && cd psb
   ```

3. **Create `config.py` File**
   ```bash
   nano config.py
   ```

   Add the required variables:
   ```python
   BOT_TOKEN = "your_bot_token"
   TELEGRAM_API = 12345678
   TELEGRAM_HASH = "your_telegram_api_hash"
   DATABASE_URL = "your_mongodb_url"
   OWNER_ID = 123456789
   UPSTREAM_REPO = "https://github.com/ImKrishana/Poster-Scraper-Bot"
   UPSTREAM_BRANCH = "main"
   ```

4. **Commit Changes**
   ```bash
   git add . -f
   git commit -m "thezake"
   ```

5. **Create Heroku App**
   ```bash
   heroku create APP_NAME
   ```

6. **Add Remote**
   ```bash
   heroku git:remote -a APP_NAME
   ```

7. **Set Container Stack**
   ```bash
   heroku stack:set container
   ```

8. **Deploy**
   ```bash
   git push heroku main -f
   ```

9. **Check Logs**
   ```bash
   heroku logs --tail
   ```

</details>

## Variables 
<details>
<summary><strong>Click Here</strong></summary>

### Required Variables

- **`TELEGRAM_API`** - Get this from [my.telegram.org](https://my.telegram.org)
- **`TELEGRAM_HASH`** - Get this from [my.telegram.org](https://my.telegram.org)
- **`BOT_TOKEN`** - Get this from [@BotFather](https://t.me/BotFather)
- **`DATABASE_URL`** - MongoDB database URL
- **`OWNER_ID`** - Your Telegram user ID
- **`UPSTREAM_REPO`** - Your forked repository URL for auto update
- **`UPSTREAM_BRANCH`** - Repository branch name
  
### Optional Variables

- **`AUTHORIZED_CHATS`** - Comma-separated Telegram chat IDs where the bot is authorized to work
- **`HELPER_TOKENS`** - Space-separated helper bot tokens used to read/scan target chats
- **`USER_SESSION_STRING`** - User session used only to delete duplicate messages
- **`AUTHOR_NAME`** - Name displayed as the author of generated content
- **`AUTHOR_URL`** - Telegram or website URL associated with the author
- **`BASE_URL`** - Base URL used for web access or external services
- **`BASE_URL_PORT`** - Port used by the base URL/web server
- **`CMD_SUFFIX`** - Suffix added to bot commands
- **`DEFAULT_LANG`** - Default language used by the bot
- **`PROTECTED_API`** - Shortener Bypass API endpoint or URL
- **`PUBLIC_MODE`** - Enables or disables public mode for the bot
- **`SUDO_USERS`** - Telegram user IDs with sudo/admin-level access
- **`SET_COMMANDS`** - Automatically sets bot commands in Telegram
- **`TG_PROXY`** - Telegram proxy configuration
- **`TIMEZONE`** - Timezone used by the bot for date and time operations
- **`UPDATE_PKGS`** - Automatically updates required Python packages
- **`VERIFY_TIMEOUT`** - Timeout duration for user verification


- **`HELPER_BOT_PROXIES`** - Optional proxy configuration for helper bots
- **`DEDUP_CHANNELS`** - Optional comma-separated default DeDup chat IDs
- **`BATCH_SIZE`** - Number of message IDs processed in one scan batch
- **`PROGRESS_UPDATE_INTERVAL`** - Status-message refresh interval in seconds
- **`CHECKPOINT_INTERVAL`** - Scan checkpoint interval in seconds
- **`HASH_CHUNK_SIZE`** - File hashing read chunk size in bytes
- **`HASH_HEAD_BYTES`** - Bytes sampled from the start of a file
- **`HASH_MIDDLE_BYTES`** - Bytes sampled from the middle of a file
- **`HASH_TAIL_BYTES`** - Bytes sampled from the end of a file
- **`FLOOD_WAIT_MULTIPLIER`** - Flood-wait cooldown multiplier
- **`MAX_RETRIES`** - Maximum retries for recoverable failures
- **`REQUEST_TIMEOUT`** - Telegram request timeout in seconds
- **`TIMEOUT_COOLDOWN`** - Cooldown after a request timeout in seconds
- **`DELETE_BATCH_SIZE`** - Maximum message IDs per deletion request
- **`SENTINEL_TEXT`** - Temporary text used to identify the newest chat message
- **`SESSION_DIR`** - Directory used for Telegram session files
- **`FILES_COLLECTION`** - MongoDB collection for scanned media records
- **`PROGRESS_COLLECTION`** - MongoDB collection for scan checkpoints
- **`PROGRESS_DOC_ID`** - MongoDB ID for the active scan checkpoint
</details>

## 💖 Donate 

If you find this project useful and would like to support its development, you can make a donation:

### 💰 Binance
**Binance Pay ID:** `1227156725`

### 🇮🇳 UPI
**UPI ID:** `ftkrishana@fam`

Every contribution is greatly appreciated and helps keep the project maintained and improved. ❤️
