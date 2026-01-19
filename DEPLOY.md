# 🚀 Деплой URL Monitor

## Быстрый деплой с Docker

### 1. Установи Docker на сервере

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Перелогинься или выполни: newgrp docker
```

### 2. Загрузи проект на сервер

```bash
# Вариант A: через git
git clone <твой-репозиторий> proxy-checker
cd proxy-checker

# Вариант B: через scp (с локальной машины)
scp -r /путь/к/proxy-checker user@server:/home/user/proxy-checker
```

### 3. Настрой переменные окружения

```bash
# Сгенерируй секретный ключ
openssl rand -hex 32

# Создай .env файл
cat > .env << 'EOF'
SECRET_KEY=<вставь-сгенерированный-ключ>
EOF
```

### 4. Перенеси базу данных (если есть)

```bash
# Создай папку для данных
mkdir -p data

# Скопируй существующую БД (с локальной машины)
scp url_monitor.db user@server:/home/user/proxy-checker/data/
```

### 5. Запусти!

```bash
docker compose up -d --build
```

Приложение будет доступно на http://your-server:8000

---

## Настройка домена с HTTPS (nginx + Let's Encrypt)

### 1. Установи nginx и certbot

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Создай конфиг nginx

```bash
sudo nano /etc/nginx/sites-available/proxy-checker
```

Вставь:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

### 3. Активируй сайт

```bash
sudo ln -s /etc/nginx/sites-available/proxy-checker /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Получи SSL сертификат

```bash
sudo certbot --nginx -d your-domain.com
```

Готово! Сайт доступен по https://your-domain.com

---

## Команды управления

```bash
# Запуск
docker compose up -d

# Остановка
docker compose down

# Логи
docker compose logs -f

# Перезапуск
docker compose restart

# Обновление (после изменений в коде)
docker compose up -d --build

# Бэкап базы данных
cp data/url_monitor.db data/url_monitor_backup_$(date +%Y%m%d).db
```

---

## Учётные данные по умолчанию

- **Логин:** `main-admin`
- **Пароль:** `£W"'71tvg\4mZS1ohX`

⚠️ **ВАЖНО:** Смени пароль после первого входа через раздел "Пользователи"!

---

## Структура файлов на сервере

```
proxy-checker/
├── app/                    # Код приложения
├── data/                   # Папка с БД (volume)
│   └── url_monitor.db      # База данных SQLite
├── docker-compose.yml
├── Dockerfile
└── .env                    # Секретные переменные (не в git!)
```

