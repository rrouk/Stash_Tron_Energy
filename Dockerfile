# Используем официальный образ Python как базовый
FROM python:3.11-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
# Копируем файл бота
COPY botss.py .

# Запускаем бота при старте контейнера
CMD ["python", "botss.py"]
# --- КОНЕЦ ИЗМЕНЕНИЙ ---
