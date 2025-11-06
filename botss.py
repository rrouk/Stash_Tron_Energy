import time
import threading
import requests
import telebot
from telebot import types
from tronpy import Tron
from tronpy.providers import HTTPProvider
from tronpy.keys import PrivateKey
import os
import logging
import json
from datetime import datetime, timedelta, timezone

# Часовой пояс UTC+3
zone_time = int(os.getenv("zone_time"))
TZ_MOSCOW = timezone(timedelta(hours=zone_time))



# Настройка логирования в начале файла (должна быть)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(processName)s - %(name)s - %(levelname)s - %(message)s')

path_json_otl = "/app/scheduled_tasks.json"



# ================== Настройки Telegram ==================
# Получаем токен бота
try:
    API_TOKEN = os.getenv("API_TOKEN")
except:
    logging.info("API_TOKEN бота не задан в .env")
bot = telebot.TeleBot(API_TOKEN)



# Преобразуем строку ADMIN_IDS в список целых чисел
try:
    ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS").split(',')]
except:
    logging.info("❌ Ошибка: ADMIN_IDS должны быть заданы как список чисел через запятую в .env.")
    ADMIN_IDS = []





# ================== Глобальные переменные из .env ==================
try:
    api_key_tronscan = os.getenv("API_KEY_TRONSCAN")
    api_key_trongrid = os.getenv("API_KEY_TRONGRID")
    priv_key_my_hex = os.getenv("PRIV_KEY_MY_HEX")
    PERM_ID = int(os.getenv("PERM_ID"))
    main_wallet = os.getenv("MAIN_WALLET")
    stashing_target = os.getenv("STASHING_TARGET")
    
    priv_key_my = PrivateKey(bytes.fromhex(priv_key_my_hex))
    
except Exception as e:
    logging.info(f"❌ Критическая ошибка: Не удалось загрузить все необходимые переменные из .env. Проверьте .env. Ошибка: {e}")


# ================== Логирование ==================
def log_error_crash(msg):
    # Используем standard logging (будет видно в Docker logs)
    logging.error(f"❌ {msg}")
    
    log_message = f"[{datetime.now(TZ_MOSCOW).strftime('%Y-%m-%d %H:%M:%S')}] ❌ {msg}"
    # Отправка администраторам
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, log_message)
        except telebot.apihelper.ApiTelegramException as e:
            # Используем standard logging для ошибки отправки
            logging.info(f"[ERROR] Could not send message to {admin_id}: {e}")

def log_work(msg):
    # Используем standard logging (будет видно в Docker logs)
    logging.info(f"✅ {msg}")
    
    log_message = f"[{datetime.now(TZ_MOSCOW).strftime('%Y-%m-%d %H:%M:%S')}] ✅ {msg}"
    # Отправка администраторам
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, log_message)
        except telebot.apihelper.ApiTelegramException as e:
            # Используем standard logging для ошибки отправки
            logging.info(f"[ERROR] Could not send message to {admin_id}: {e}")


# ================== Tron функции ==================
# (Используют глобальные переменные api_key_trongrid, api_key_tronscan, PERM_ID, priv_key_my)

def get_energy_info(addressEN):
    try:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "TRON-PRO-API-KEY": api_key_trongrid
        }
        url = "https://api.trongrid.io/wallet/getaccountresource"
        payload = {"address": addressEN, "visible": True}
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            log_error_crash(f"Ошибка getaccountresource: {response.status_code}, {response.text}")
            return 0,0,0,0,0,0
        data = response.json()
        energy_used = data.get("EnergyUsed",0)
        energy_limit = data.get("EnergyLimit",0)
        delegated_energy_from_others = data.get("account_resource.acquired_delegated_frozenV2_balance_for_energy",0)
        delegated_energy_to_others = data.get("delegatedFrozenV2BalanceForEnergy",0)
        total_energy_limit = data.get("TotalEnergyLimit",0)
        total_energy_weight = data.get("TotalEnergyWeight",0)
        trx_energy_price = total_energy_weight / total_energy_limit if total_energy_limit > 0 else 0
        all_energy = energy_limit + delegated_energy_from_others
        free_energy_ac = all_energy - energy_used if all_energy > energy_used else 0
        unused_slot = int(free_energy_ac * trx_energy_price) if trx_energy_price > 0 else 0
        return free_energy_ac, trx_energy_price, unused_slot, all_energy, energy_used, delegated_energy_from_others
    except Exception as e:
        log_error_crash(f"Ошибка в get_energy_info: {e}")
        return 0,0,0,0,0,0

def get_max_delegatable_trx(addressEN):
    url = "https://api.trongrid.io/wallet/getcandelegatedmaxsize"
    payload = {"owner_address": addressEN,"type":1,"visible":True}
    headers = {"accept":"application/json","content-type":"application/json","TRON-PRO-API-KEY": api_key_trongrid}
    try:
        response = requests.post(url,json=payload,headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("max_size",0)
        else:
            log_error_crash(f"Ошибка getcandelegatedmaxsize: {response.status_code} {response.text}")
            return 0
    except Exception as e:
        log_error_crash(f"Ошибка getcandelegatedmaxsize: {e}")
        return 0

def create_delegate_energy_txid(addressEN, receiver_address_delegate_my, delegate_my_trx):
    try:
        provider = HTTPProvider(api_key=api_key_trongrid)
        client = Tron(provider=provider)
        amount_trx = int(delegate_my_trx * 1_000_000)
        txn = (client.trx.delegate_resource(addressEN,receiver_address_delegate_my,amount_trx,resource='ENERGY')
               .permission_id(PERM_ID).build().sign(priv_key_my))
        response = txn.broadcast().wait()
        if 'id' in response:
            log_work(f"Энергия делегирована на {receiver_address_delegate_my} в размере {delegate_my_trx:.2f} TRX")
            return txn.txid, True
        else:
            log_error_crash(f"Ошибка делегации: {response}")
            return None, False
    except Exception as e:
        log_error_crash(f"Ошибка делегации: {e}")
        return None, False

def create_undelegate_energy_txid(addressEN, receiver_address_delegate_my, undelegate_trx):
    try:
        provider = HTTPProvider(api_key=api_key_trongrid)
        client = Tron(provider=provider)
        amount_trx = int(undelegate_trx * 1_000_000)
        txn = (client.trx.undelegate_resource(addressEN,receiver_address_delegate_my,amount_trx,resource='ENERGY')
               .permission_id(PERM_ID).build().sign(priv_key_my))
        response = txn.broadcast().wait()
        if 'id' in response:
            log_work(f"Отозвана делегация {undelegate_trx:.2f} TRX с {receiver_address_delegate_my}")
            return txn.txid, True
        else:
            log_error_crash(f"Ошибка отзыва делегации: {response}")
            return None, False
    except Exception as e:
        log_error_crash(f"Ошибка отзыва делегации: {e}")
        return None, False


# ================== Декоратор для проверки админов ==================
def admin_only(func):
    def wrapper(message,*args,**kwargs):
        if message.from_user.id not in ADMIN_IDS:
            bot.send_message(message.chat.id,"⛔ У тебя нет доступа")
            return
        return func(message,*args,**kwargs)
    return wrapper

# ================== Клавиатура снизу ==================
def bottom_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=3, resize_keyboard=True, one_time_keyboard=False)
    markup.add(
        types.KeyboardButton("Спрятать 📤"),
        types.KeyboardButton("Вернуть 📥"),
        types.KeyboardButton("Отложить ⏳"),
        types.KeyboardButton("Показать Отложки 📋"),
        types.KeyboardButton("Удалить Отложки ❌")
    )
    return markup

# ================== Telegram обработчики ==================
@bot.message_handler(commands=["start"])
@admin_only
def start_bot_message(message):
    text = (
        "🤖 Бот Tron Energy Stasher\n\n"
        f"Адрес-Котлета: `{main_wallet}`\n"
        f"Адрес-Тайник: `{stashing_target}`\n\n"
        "Параметры заданы в .env и не могут быть изменены через команды."
    )

    bot.send_message(message.chat.id, text, reply_markup=bottom_keyboard(), parse_mode='Markdown')



# ================== Логика "Спрятать" (Делегирование) ==================
@bot.message_handler(func=lambda m: m.text=="Спрятать 📤")
@admin_only
def stash_energy(message):
    bot.send_message(message.chat.id, "⏳ Рассчитываю максимальный объем для делегирования...")
    
    # 1. Получаем максимальный объем TRX для делегирования (в sun)
    trx_deleg_max_sun = get_max_delegatable_trx(main_wallet)
    
    if trx_deleg_max_sun == 0:
        bot.send_message(message.chat.id, "✅ Нечего делегировать. Вся энергия уже спрятана, или нет свободного TRX.")
        return

    # Переводим в TRX
    trx_to_delegate = int(trx_deleg_max_sun / 1_000_000)
    
    # 2. Выполняем делегирование
    txid, ok = create_delegate_energy_txid(main_wallet, stashing_target, trx_to_delegate)

    if ok:
        bot.send_message(message.chat.id, 
                         f"✅ Спрятано!\n\n"
                         f"Делегировано: {trx_to_delegate:.2f} TRX\n"
                         f"На адрес: `{stashing_target}`\n"
                         f"TXID: `{txid}`",
                         parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "❌ Произошла ошибка при делегировании. Проверьте логи.")


# ================== Логика "Вернуть" (Отзыв) ==================
@bot.message_handler(func=lambda m: m.text=="Вернуть 📥")
@admin_only
def reclaim_energy(message):
    bot.send_message(message.chat.id, "⏳ Проверяю активные делегации на Адрес-Тайник...")
    
    # 1. Получаем список всех делегаций с нашего main_wallet
    url = f"https://apilist.tronscanapi.com/api/account/resourcev2?address={main_wallet}&type=2&resourceType=2"
    headers = {"TRON-PRO-API-KEY": api_key_tronscan}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise Exception(f"TronScan API Error: {response.status_code}, {response.text}")
            
        data = response.json()
        delegations_list = data.get("data", [])
        
    except Exception as e:
        log_error_crash(f"Ошибка запроса списка делегаций к TronScan: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить список делегаций. Проверьте логи.")
        return
        
    # 2. Ищем делегацию, сделанную на stashing_target
    target_delegation = None
    for delegation in delegations_list:
        if delegation.get("receiverAddress") == stashing_target:
            target_delegation = delegation
            break

    if not target_delegation:
        bot.send_message(message.chat.id, "✅ Нет активных делегаций на Адрес-Тайник для отзыва.")
        return
        
    # 3. Выполняем отзыв
    amount_in_trx = target_delegation.get("balance", 0) / 1_000_000
    amount_in_trx = int(amount_in_trx) # Округляем до целого TRX

    if amount_in_trx <= 0:
        bot.send_message(message.chat.id, "❌ Обнаружен нулевой объем делегации на Адрес-Тайник.")
        return

    bot.send_message(message.chat.id, f"🔄 Запускаю отзыв делегации: {amount_in_trx} TRX с `{stashing_target}`...", parse_mode='Markdown')
    
    txid, ok = create_undelegate_energy_txid(main_wallet, stashing_target, amount_in_trx)

    if ok:
        bot.send_message(message.chat.id, 
                         f"✅ Возвращено!\n\n"
                         f"Отозвано: {amount_in_trx} TRX\n"
                         f"С адреса: `{stashing_target}`\n"
                         f"TXID: `{txid}`",
                         parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "❌ Произошла ошибка при отзыве делегации. Проверьте логи.")






# Функции обработки очереди
def load_scheduled_tasks():
    try:
        with open(path_json_otl, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Приведение строк к datetime
            for task in data:
                if isinstance(task["schedule_time"], str):
                    task["schedule_time"] = datetime.fromisoformat(task["schedule_time"])
                if isinstance(task["return_time"], str):
                    task["return_time"] = datetime.fromisoformat(task["return_time"])
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_scheduled_tasks(tasks):
    # Конвертируем datetime в строки для JSON
    serializable = []
    for task in tasks:
        t = task.copy()
        t["schedule_time"] = t["schedule_time"].isoformat()
        t["return_time"] = t["return_time"].isoformat()
        serializable.append(t)
    with open(path_json_otl, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)




@bot.message_handler(func=lambda m: m.text == "Отложить ⏳")
@admin_only
def delayed_stash_start(message):
    bot.send_message(
        message.chat.id,
        "⏳ Введите дату и время делегирования в формате:\n`YYYY-MM-DD HH:MM`\n(время будет интерпретироваться как UTC+3)",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(message, delayed_stash_step1)

def delayed_stash_step1(message):
    try:
        naive_dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        schedule_time = naive_dt.replace(tzinfo=TZ_MOSCOW)
        if schedule_time <= datetime.now(TZ_MOSCOW):
            bot.send_message(message.chat.id, "❌ Время должно быть в будущем!")
            return
        bot.send_message(
            message.chat.id,
            "⏳ Введите длительность удержания (в минутах, целое число, например: `6`):"
        )
        bot.register_next_step_handler(message, delayed_stash_step2, schedule_time)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат даты. Попробуйте снова.")
        return

def delayed_stash_step2(message, schedule_time):
    try:
        hold_minutes = int(message.text.strip())
        if hold_minutes <= 0:
            raise ValueError
        return_time = schedule_time + timedelta(minutes=hold_minutes)

        tasks = load_scheduled_tasks()
        tasks.append({
            "schedule_time": schedule_time,
            "return_time": return_time,
            "executed": False,
            "delegated": False,
            "returned": False,
            "txid_delegate": None,
            "txid_return": None
        })
        save_scheduled_tasks(tasks)

        bot.send_message(
            message.chat.id,
            f"✅ Задача запланирована!\n"
            f"Делегировать: {schedule_time.strftime('%Y-%m-%d %H:%M')} (UTC+3)\n"
            f"Вернуть через: {hold_minutes} мин → {return_time.strftime('%Y-%m-%d %H:%M')} (UTC+3)"
        )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите положительное целое число минут (например: 30, 90, 120).")
        return



def _send_tasks_list_message(message):
    tasks = load_scheduled_tasks()
    
    # 1. Отфильтровываем все невыполненные задачи
    active_tasks = [t for t in tasks if not t.get("executed")]
    
    if not active_tasks:
        bot.send_message(message.chat.id, "✅ Список активных отложенных задач пуст.")
        return
        
    output = "📜 **Активные Отложенные Задачи (UTC+3):**\n\n"
    markup = types.InlineKeyboardMarkup()
    
    # i будет соответствовать индексу в списке active_tasks
    for i, task in enumerate(active_tasks):
        schedule_time_str = task["schedule_time"].strftime('%Y-%m-%d %H:%M') if isinstance(task["schedule_time"], datetime) else str(task["schedule_time"])
        return_time_str = task["return_time"].strftime('%Y-%m-%d %H:%M') if isinstance(task["return_time"], datetime) else str(task["return_time"])
        
        status = ""
        if task.get("delegated") and not task.get("returned"):
            status = " (Спрятано, ждет возврата)"
        elif not task.get("delegated"):
            status = " (Ждет делегирования)"
        
        markup.add(types.InlineKeyboardButton(f"❌ Удалить задачу #{i+1}", callback_data=f"delete_task_{i}"))
        
        output += (
            f"**Задача #{i+1}**{status}\n"
            f"Делегировать в: `{schedule_time_str}`\n"
            f"Вернуть в: `{return_time_str}`\n"
            "----\n"
        )
        
    markup.add(types.InlineKeyboardButton("🗑️ Удалить ВСЕ активные", callback_data="confirm_delete_all_tasks"))

    bot.send_message(message.chat.id, output, reply_markup=markup, parse_mode='Markdown')



# Обработчик кнопки, вызывающий вспомогательную функцию (С ДЕКОРАТОРОМ)
@bot.message_handler(func=lambda m: m.text == "Показать Отложки 📋")
@admin_only
def show_delayed_tasks(message):
    _send_tasks_list_message(message)




# Обработчик кнопки (С ДЕКОРАТОРОМ)
@bot.message_handler(func=lambda m: m.text == "Удалить Отложки ❌")
@admin_only
def delete_all_delayed_tasks_confirm(message):
    tasks = load_scheduled_tasks()
    active_tasks_count = sum(1 for t in tasks if not t.get("executed"))
    
    if active_tasks_count == 0:
        bot.send_message(message.chat.id, "✅ Нет активных отложенных задач для удаления.")
        return
        
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Да, удалить ВСЕ", callback_data="confirm_delete_all_tasks"),
        types.InlineKeyboardButton("❌ Нет, отмена", callback_data="cancel")
    )
    
    bot.send_message(
        message.chat.id, 
        f"⚠️ **ВНИМАНИЕ!** Вы уверены, что хотите удалить ВСЕ {active_tasks_count} активных отложенных задач?\n\n"
        "*(Это не отменит уже произошедшее делегирование, но остановит запланированный возврат)*",
        reply_markup=markup,
        parse_mode='Markdown'
    )



@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_task_') or call.data in ["confirm_delete_all_tasks", "cancel"])
@admin_only
def callback_inline(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    bot.answer_callback_query(call.id) # Убираем "часы"
    
    if call.data == "cancel":
        bot.edit_message_text("✅ Действие отменено.", chat_id, message_id)
        return

    all_tasks = load_scheduled_tasks()
    
    if call.data == "confirm_delete_all_tasks":
        # Создаем новый список, оставляя только выполненные задачи (удаляем все активные)
        new_all_tasks = [t for t in all_tasks if t.get("executed")]
        
        if len(new_all_tasks) < len(all_tasks):
            save_scheduled_tasks(new_all_tasks)
            try:
                 bot.edit_message_text(
                    "🗑️ **ВСЕ** активные отложенные задачи удалены.", 
                    chat_id, 
                    message_id,
                    parse_mode='Markdown'
                )
            except telebot.apihelper.ApiTelegramException: 
                bot.send_message(chat_id, "🗑️ **ВСЕ** активные отложенные задачи удалены.", parse_mode='Markdown')
            log_work("Удалены все активные отложенные задачи.")
        else:
            bot.edit_message_text("✅ Нет активных задач для удаления.", chat_id, message_id)
            
    elif call.data.startswith('delete_task_'):
        try:
            task_index_in_active_list = int(call.data.split('_')[2])
            
            active_tasks = [t for t in all_tasks if not t.get("executed")]
            
            if task_index_in_active_list < 0 or task_index_in_active_list >= len(active_tasks):
                bot.send_message(chat_id, "❌ Задача не найдена.")
                return

            deleted_count = 0
            new_all_tasks = []
            
            for t in all_tasks:
                if not t.get("executed"):
                    if deleted_count == task_index_in_active_list:
                        deleted_count += 1
                        continue 
                    deleted_count += 1
                new_all_tasks.append(t)
                
            if deleted_count > task_index_in_active_list:
                save_scheduled_tasks(new_all_tasks)
                log_work(f"Удалена отложенная задача #{task_index_in_active_list+1}.")
                
                try:
                    bot.delete_message(chat_id, message_id)
                except Exception:
                    pass 
                
                _send_tasks_list_message(call.message) 
                
            else:
                bot.send_message(chat_id, "❌ Не удалось найти и удалить задачу.")
                
        except Exception as e:
            log_error_crash(f"Ошибка при удалении задачи: {e}")
            bot.send_message(chat_id, "❌ Произошла ошибка удаления.")








def scheduler_worker():
    while True:
        try:
            now = datetime.now(TZ_MOSCOW)
            tasks = load_scheduled_tasks()
            updated = False

            for task in tasks:
                if not task["executed"]:
                    # Этап 1: делегировать?
                    if not task["delegated"] and now >= task["schedule_time"]:
                        log_work("⏳ Выполняется отложенное делегирование...")
                        # Получаем макс TRX
                        trx_deleg_max_sun = get_max_delegatable_trx(main_wallet)
                        if trx_deleg_max_sun > 0:
                            trx_to_delegate = int(trx_deleg_max_sun / 1_000_000)
                            txid, ok = create_delegate_energy_txid(main_wallet, stashing_target, trx_to_delegate)
                            if ok:
                                task["delegated"] = True
                                task["txid_delegate"] = txid
                                log_work(f"✅ Отложенное делегирование выполнено: {trx_to_delegate} TRX")
                            else:
                                log_error_crash("❌ Отложенное делегирование не удалось!")
                        else:
                            log_work("⚠️ Отложенное делегирование: нечего делегировать.")
                        updated = True

                    # Этап 2: вернуть?
                    if task["delegated"] and not task["returned"] and now >= task["return_time"]:
                        log_work("⏳ Выполняется отложенный возврат энергии...")
                        # Получаем текущую делегацию
                        url = f"https://apilist.tronscanapi.com/api/account/resourcev2?address={main_wallet}&type=2&resourceType=2"
                        headers = {"TRON-PRO-API-KEY": api_key_tronscan}
                        try:
                            resp = requests.get(url, headers=headers)
                            data = resp.json()
                            amount_in_trx = 0
                            for d in data.get("data", []):
                                if d.get("receiverAddress") == stashing_target:
                                    amount_in_trx = int(d.get("balance", 0) / 1_000_000)
                                    break
                            if amount_in_trx > 0:
                                txid, ok = create_undelegate_energy_txid(main_wallet, stashing_target, amount_in_trx)
                                if ok:
                                    task["returned"] = True
                                    task["txid_return"] = txid
                                    log_work(f"✅ Отложенный возврат выполнен: {amount_in_trx} TRX")
                                else:
                                    log_error_crash("❌ Отложенный возврат не удался!")
                            else:
                                log_work("⚠️ Отложенный возврат: делегация уже отсутствует.")
                        except Exception as e:
                            log_error_crash(f"❌ Ошибка при отложенном возврате: {e}")
                        updated = True

                    # Завершить задачу
                    if task["delegated"] and task["returned"]:
                        task["executed"] = True

            if updated:
                save_scheduled_tasks(tasks)

        except Exception as e:
            log_error_crash(f"❌ Ошибка в scheduler_worker: {e}")

        time.sleep(30)  # Проверка каждые 30 секунд
		




scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
scheduler_thread.start()

# === Запуск бота ===
logging.info("Бот запущен...")

while True:
    try:
        logging.info("Бот запущен и ожидает новые посты и команды...")
        bot.polling(none_stop=True, interval=0, timeout=40)

    except Exception as e:
        # Логирование критической ошибки
        logging.info(f"*** КРИТИЧЕСКАЯ ОШИБКА ВНЕ ПОЛЛИНГА: {e} ***")
        # Ждём перед попыткой перезапуска
        time.sleep(15)
