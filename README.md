# AnyMusicToYandexMusic

Telegram-бот для загрузки музыки с YouTube в плейлисты Яндекс Музыки.

Отправь название песни — бот найдёт её на YouTube, скачает аудио и загрузит в выбранный плейлист твоего аккаунта Яндекс Музыки.

## Возможности

- Поиск треков на YouTube
- Скачивание аудио в MP3 320kbps
- Загрузка в любой плейлист Яндекс Музыки
- Мультипользовательский режим — каждый привязывает свой аккаунт ЯМ
- Работает на Windows и Linux

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и справка |
| `/token ТОКЕН` | Привязать аккаунт Яндекс Музыки |
| `/playlists` | Показать список плейлистов |
| `/logout` | Отвязать аккаунт |

## Быстрый старт (Windows)

1. Запусти `start.bat`
2. При первом запуске введи токен Telegram-бота
3. Chrome откроется один раз — залогинься в Google и закрой его
4. Бот запустится
5. Отправь боту `/token ТВОЙ_ТОКЕН` для привязки Яндекс Музыки

## Ручная установка

### Требования

- Python 3.10+
- Node.js (для решения JS-челленджей YouTube)
- FFmpeg
- Google Chrome (для одноразовой настройки cookies на Windows)

### Установка

```bash
git clone https://github.com/your-username/AnyMusicToYandexMusic.git
cd AnyMusicToYandexMusic
pip install -r requirements.txt
```

### Настройка

1. Скопируй `.env.example` в `.env` и вставь токен Telegram-бота:

```
TG_BOT_TOKEN=123456:ABC-DEF...
```

2. Настрой YouTube cookies (один раз):

**Windows:** запусти `python setup_cookies.py`, залогинься в Google в открывшемся Chrome, закрой его.

**Linux:** на машине с браузером экспортируй cookies и скопируй файл на сервер:

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://www.youtube.com"
scp cookies.txt server:/path/to/bot/
```

3. Запусти бота:

```bash
python bot.py
```

## Получение токенов

### Telegram Bot Token

Создай бота через [@BotFather](https://t.me/BotFather) и скопируй токен.

### Yandex Music Token

Перейди по ссылке и авторизуйся:

```
https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
```

Токен будет в адресной строке после `access_token=`.

## Структура проекта

```
├── bot.py              # Основной код бота
├── setup_cookies.py    # Одноразовая настройка YouTube cookies
├── start.bat           # Лаунчер для Windows
├── requirements.txt    # Python-зависимости
├── .env.example        # Шаблон переменных окружения
└── .gitignore
```

## Как это работает

1. Пользователь отправляет название песни
2. Бот ищет на YouTube через yt-dlp
3. Пользователь выбирает трек из результатов
4. Пользователь выбирает плейлист Яндекс Музыки
5. Бот скачивает аудио, конвертирует в MP3 и загружает в плейлист через API Яндекс Музыки
