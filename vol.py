#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import json
import subprocess
import sys
import threading
import time
import pythoncom
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

# --- НАСТРОЙКИ ---
HTTP_PORT = 9999
SHUTDOWN_TOKEN = "CHANGE_ME_SECRET_2026"   # ← поменяйте!
# --- КОНЕЦ НАСТРОЕК ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Кэш COM-интерфейса (иначе каждый запрос тормозит на 100-500 мс).
# Доступ защищён блокировкой — COM-объект pycaw не потокобезопасен.
_volume_cache = None
_volume_lock = threading.Lock()


def get_volume_interface():
    global _volume_cache
    if _volume_cache is None:
        devices = AudioUtilities.GetSpeakers()
        _volume_cache = devices.EndpointVolume.QueryInterface(IAudioEndpointVolume)
    return _volume_cache


def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    with _volume_lock:
        volume = get_volume_interface()
        volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
    return percent


def get_volume():
    with _volume_lock:
        volume = get_volume_interface()
        return int(round(volume.GetMasterVolumeLevelScalar() * 100))


def set_mute(mute):
    with _volume_lock:
        volume = get_volume_interface()
        volume.SetMute(1 if mute else 0, None)
    return bool(mute)


def get_mute():
    with _volume_lock:
        volume = get_volume_interface()
        return bool(volume.GetMute())


def with_com_init(func):
    """
    Декоратор: инициализирует COM в текущем потоке перед вызовом func
    и освобождает COM после. Нужен, потому что Flask/ThreadingHTTPServer
    запускает каждый запрос в отдельном потоке, а COM-инициализация
    НЕ наследуется потоками.
    """
    def wrapper(*args, **kwargs):
        pythoncom.CoInitialize()
        try:
            return func(*args, **kwargs)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    return wrapper


class VolumeHTTPHandler(BaseHTTPRequestHandler):
    # ВАЖНО: НЕ включаем HTTP/1.1 (keep-alive).
    # По умолчанию BaseHTTPRequestHandler использует HTTP/1.0 и закрывает
    # соединение после каждого ответа — это избавляет от «протухших» сокетов
    # на клиенте, которые приводили к зависанию через ~минуту.
    # protocol_version = "HTTP/1.1"

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(status_code)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        finally:
            # Форсируем закрытие после каждого ответа
            self.close_connection = True

    # ---------- действия ----------
    def _do_shutdown(self):
        # Этот метод запускается в отдельном потоке — инициализируем COM,
        # чтобы случайные обращения к pycaw в этом потоке не падали.
        pythoncom.CoInitialize()
        try:
            logging.warning("Начинаю выключение ПК…")
            time.sleep(3)
            logging.warning("Вызываю shutdown /s /t 5")
            if sys.platform == "win32":
                subprocess.Popen(["shutdown", "/s", "/t", "5",
                                  "/c", "Remote shutdown via API"])
            else:
                subprocess.Popen(["shutdown", "-h", "+1"])
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _do_reboot(self):
        pythoncom.CoInitialize()
        try:
            logging.warning("Начинаю перезагрузку ПК…")
            time.sleep(3)
            logging.warning("Вызываю shutdown /r /t 5")
            if sys.platform == "win32":
                subprocess.Popen(["shutdown", "/r", "/t", "5",
                                  "/c", "Remote reboot via API"])
            else:
                subprocess.Popen(["shutdown", "-r", "+1"])
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _do_cancel(self):
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/a"], check=False)
        else:
            subprocess.run(["shutdown", "-c"], check=False)

    def _check_token(self, query) -> bool:
        token = (query.get('token') or [''])[0]
        if token != SHUTDOWN_TOKEN:
            logging.warning(f"Отказ: неверный токен от {self.address_string()}")
            self._send_json(403, {'status': 'error', 'message': 'invalid token'})
            return False
        return True

    @with_com_init
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.strip('/').lower()
        query = parse_qs(parsed.query)
        parts = path.split('/')

        t0 = time.time()
        logging.info(f"→ GET /{path} query={query} from {self.address_string()}")

        try:
            # ---------- ГРОМКОСТЬ ----------
            if path == 'volume':
                self._send_json(200, {
                    'status': 'success',
                    'volume': get_volume(),
                    'mute': get_mute()
                })

            elif len(parts) == 2 and parts[0] == 'volume':
                value = int(parts[1])
                result = set_volume(value)
                self._send_json(200, {
                    'status': 'success',
                    'action': 'set_volume',
                    'volume': result
                })

            elif len(parts) == 3 and parts[0] == 'volume' and parts[1] == 'up':
                step = int(parts[2])
                current = get_volume()
                result = set_volume(current + step)
                self._send_json(200, {
                    'status': 'success',
                    'action': 'volume_up',
                    'from': current,
                    'to': result
                })

            elif len(parts) == 3 and parts[0] == 'volume' and parts[1] == 'down':
                step = int(parts[2])
                current = get_volume()
                result = set_volume(current - step)
                self._send_json(200, {
                    'status': 'success',
                    'action': 'volume_down',
                    'from': current,
                    'to': result
                })

            elif path == 'mute/on':
                set_mute(True)
                self._send_json(200, {'status': 'success', 'mute': True})

            elif path == 'mute/off':
                set_mute(False)
                self._send_json(200, {'status': 'success', 'mute': False})

            # ---------- ПИТАНИЕ ПК ----------
            elif path == 'shutdown':
                if not self._check_token(query):
                    return
                logging.warning(f"→ ОТКЛЮЧЕНИЕ ПК по запросу от {self.address_string()}")
                self._send_json(200, {
                    'status': 'success',
                    'action': 'shutdown',
                    'message': 'ПК будет выключен через 5 секунд'
                })
                threading.Thread(target=self._do_shutdown, daemon=True).start()

            elif path == 'reboot':
                if not self._check_token(query):
                    return
                logging.warning(f"→ ПЕРЕЗАГРУЗКА ПК по запросу от {self.address_string()}")
                self._send_json(200, {
                    'status': 'success',
                    'action': 'reboot',
                    'message': 'ПК будет перезагружен через 5 секунд'
                })
                threading.Thread(target=self._do_reboot, daemon=True).start()

            elif path == 'cancel':
                if not self._check_token(query):
                    return
                logging.warning(f"→ ОТМЕНА выключения/перезагрузки по запросу от {self.address_string()}")
                self._do_cancel()
                self._send_json(200, {'status': 'success', 'action': 'cancel'})

            # ---------- HELP ----------
            elif path in ('', 'help'):
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                body = self._help_page().encode('utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            else:
                self._send_json(404, {
                    'status': 'error',
                    'message': f'Неизвестный путь: /{path}',
                    'available': [
                        '/volume', '/volume/50', '/volume/up/5', '/volume/down/5',
                        '/mute/on', '/mute/off',
                        '/shutdown?token=...', '/reboot?token=...', '/cancel?token=...',
                        '/help'
                    ]
                })

        except ValueError:
            self._send_json(400, {
                'status': 'error',
                'message': 'Неверное значение. Используйте целое число 0-100.'
            })
        except Exception as e:
            logging.error(f"Ошибка: {e}")
            self._send_json(500, {'status': 'error', 'message': str(e)})
        finally:
            dt = (time.time() - t0) * 1000
            logging.info(f"← GET /{path} обработан за {dt:.0f} мс")

    def _help_page(self):
        return """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Volume Control</title></head>
<body>
<h1>🔊 Volume &amp; Power API</h1>
<p><b>Громкость:</b> /volume, /volume/50, /volume/up/5, /volume/down/5</p>
<p><b>Mute:</b> /mute/on, /mute/off</p>
<p><b>Питание (требуется token):</b> /shutdown?token=XXX, /reboot?token=XXX, /cancel?token=XXX</p>
</body></html>"""

    def log_message(self, format, *args):
        # Свои access-логи пишем через logging в do_GET, поэтому
        # стандартный лог http.server отключаем.
        return


def main():
    try:
        # ThreadingHTTPServer: параллельные запросы не блокируют друг друга.
        # COM-объект защищён _volume_lock, а инициализация COM в каждом потоке
        # выполняется декоратором @with_com_init.
        server = ThreadingHTTPServer(('0.0.0.0', HTTP_PORT), VolumeHTTPHandler)
    except PermissionError as e:
        logging.error(f"Не удалось занять порт {HTTP_PORT}: {e}")
        logging.error("Возможные причины:")
        logging.error("  1. Порт занят другим процессом (netstat -ano | findstr :9999)")
        logging.error("  2. Порт в исключённом диапазоне Windows")
        logging.error("  3. Запустите от имени администратора")
        return

    logging.info(f"HTTP-сервер управления: http://0.0.0.0:{HTTP_PORT}")

    # Инициализируем COM в главном потоке, чтобы получить стартовое значение
    # громкости и «прогреть» кэш.
    pythoncom.CoInitialize()
    try:
        try:
            logging.info(f"Текущая громкость: {get_volume()}%")
        except Exception as e:
            logging.error(f"Ошибка получения громкости: {e}")
        logging.info("Эндпоинты: /volume, /volume/50, /volume/up/5, /volume/down/5, "
                     "/mute/on, /mute/off")
        logging.info("Питание (с токеном): /shutdown?token=..., /reboot?token=..., /cancel?token=...")
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Остановка…")
    finally:
        server.server_close()
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


if __name__ == '__main__':
    main()