WoW_soap_mysql_telegram_bot – это телеграм‑бот, который взаимодействует с MySQL-базой сервера World of Warcraft и с SOAP-интерфейсом. Основные возможности перечислены в файле README.md:

Регистрация через SOAP и привязка Telegram ID к аккаунту (строки 1‑9)

Смена пароля (строки 11‑16)

Получение информации о текущем онлайне и аптайме сервера (строки 18‑27)

Услуги для персонажей (смена пола, фракции, внешности, телепортация домой) – работают через SOAP (строки 29‑40)

Админ‑панель с отправкой произвольных SOAP-команд (строки 42‑45)

Запуск бота описан далее: установка зависимостей и настройка MySQL (строки 59‑93). Переменные для подключения к Telegram, SOAP и базе указываются в .env (пример приведён в файле).

Код бота находится в main.py. Подключение к Telegram реализовано через библиотеку aiogram, конфигурация загружается из .env. Из файла видно определение состояний конечных автоматов для регистрации, админ‑команд и т.д. (строки 60‑89).

Обмен с SOAP-сервером выполняется функцией send_soap_command, которая формирует SOAP-запрос и разбирает ответ (строки 91‑124). Информация о сервере (игроки, аптайм) извлекается парсером parse_server_info (строки 126‑136). Для работы с MySQL есть функции проверки существования аккаунтов, привязки Telegram ID, получения персонажей пользователя и определения его прав администратора (строки 138‑246).

Дальше объявлены обработчики команд. Например, при запуске /start формируется клавиатура с доступными действиями (строки 325‑347). Процесс регистрации и смены пароля реализован на строках 364‑426. Админ‑панель поддерживает отправку писем, золота, предметов, бан/разбан и перезапуск сервера (строки 428‑616).

В конце файла располагается функция main, которая запускает бота и начинает опрос Telegram (строки 623‑638).

Таким образом, бот позволяет игрокам зарегистрироваться и управлять своим аккаунтом, а администраторам — выполнять дополнительные операции через SOAP-команды, всё это на базе Python и библиотеки aiogram.

============================================

## Как запустить

1. **Создайте файл `.env`** и укажите параметры подключения к Telegram, SOAP и MySQL.
   Пример содержимого:

   ```
   TOKEN=YOUR_TELEGRAM_BOT_TOKEN
   SOAP_URL=http://127.0.0.1:7878/
   SOAP_USER=USER
   SOAP_PASS=PASSWORD
   DB_HOST=localhost
   DB_USER=acore
   DB_PASSWORD=acore
   DB_DATABASE=acore_auth
   ```

2. **Установите зависимости** из `requirements.txt`:

   ```bash
   pip install -r requirements.txt
   ```

3. **Запустите бота** командой:

   ```bash
   python3 main.py
   ```

После запуска будет выполнена функция `main()`, и бот начнёт опрашивать Telegram.
