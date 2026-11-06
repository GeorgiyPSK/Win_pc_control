# Volume & Power HTTP API

HTTP-сервер для удалённого управления громкостью, mute и питанием Windows-ПК. Работает поверх Core Audio (pycaw) и системной команды `shutdown`.

## Возможности

- Чтение и установка уровня громкости (0–100).
- Увеличение/уменьшение громкости на шаг.
- Включение/выключение mute.
- Выключение, перезагрузка и отмена запланированного выключения ПК (защищено токеном).
- Многопоточная обработка запросов.
- CORS включён — можно вызывать из браузера.
- HTML-страница `/help` со списком эндпоинтов.

## Требования

- Windows (используется Core Audio через pycaw и `shutdown.exe`).
- Python 3.7+
- Зависимости:

```bash
pip install pycaw comtypes pythoncom
pythoncom входит в пакет pywin32. При необходимости: pip install pywin32.

Запуск
bash
python vol.py
Сервер слушает 0.0.0.0:9999.

Настройка
В начале файла:

Константа	По умолчанию	Назначение
HTTP_PORT	9999	Порт HTTP-сервера
SHUTDOWN_TOKEN	CHANGE_ME_SECRET_2026	Токен для /shutdown, /reboot, /cancel
Обязательно смените SHUTDOWN_TOKEN перед использованием.

HTTP API
Базовый URL: http://<host>:9999

Громкость
Метод и путь	Действие
GET /volume	Текущая громкость и состояние mute
GET /volume/<0-100>	Установить громкость
GET /volume/up/<step>	Увеличить громкость на step
GET /volume/down/<step>	Уменьшить громкость на step
Пример ответа GET /volume:

json
{
  "status": "success",
  "volume": 42,
  "mute": false
}
Пример ответа GET /volume/up/5:

json
{
  "status": "success",
  "action": "volume_up",
  "from": 40,
  "to": 45
}
Mute
Метод и путь	Действие
GET /mute/on	Включить mute
GET /mute/off	Выключить mute
Питание ПК (требуется токен)
Метод и путь	Действие
GET /shutdown?token=...	Выключить ПК через 5 секунд
GET /reboot?token=...	Перезагрузить ПК через 5 секунд
GET /cancel?token=...	Отменить запланированное выключение/перезагрузку
При неверном токене возвращается 403.

Служебные
GET /help — HTML-страница со списком эндпоинтов.

Коды ответов
Код	Когда
200	Успех
400	Неверное значение (не целое число)
403	Неверный токен
404	Неизвестный путь
500	Внутренняя ошибка
Примеры
bash
# Текущая громкость
curl http://localhost:9999/volume

# Установить 50%
curl http://localhost:9999/volume/50

# Прибавить 5
curl http://localhost:9999/volume/up/5

# Mute
curl http://localhost:9999/mute/on

# Выключить ПК
curl "http://localhost:9999/shutdown?token=CHANGE_ME_SECRET_2026"

# Отменить выключение
curl "http://localhost:9999/cancel?token=CHANGE_ME_SECRET_2026"
Особенности реализации
COM-интерфейс pycaw кэшируется и защищён блокировкой — не потокобезопасный COM-объект не используется из нескольких потоков одновременно.

В каждом потоке выполняется CoInitialize/CoUninitialize (декоратор @with_com_init), поскольку COM-инициализация не наследуется потоками.

HTTP/1.0 без keep-alive: соединение закрывается после каждого ответа — это исключает «протухшие» сокеты на клиенте.

shutdown/reboot выполняются в отдельном потоке после отправки ответа, чтобы клиент сразу получил подтверждение.
