import os
import time
import tempfile
import re
import logging
from typing import Optional, List, Any
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Browser, Playwright
import openai

load_dotenv()

# Проверка наличия токена GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise ValueError(
        "GITHUB_TOKEN не найден. Создайте файл .env и добавьте строку: GITHUB_TOKEN=ваш_токен"
    )

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info(f"GITHUB_TOKEN загружен, первые {min(14, len(GITHUB_TOKEN))} символов: {GITHUB_TOKEN[:14]}...")


class SixmoAutoAgent:
    """
    Агент для автоматического прохождения формы на sixmo.ru.
    Динамически определяет типы полей, извлекает вопросы и варианты,
    использует LLM для генерации ответов.
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.client = openai.OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=GITHUB_TOKEN
        )
        self.model_name = "gpt-4o-mini"  # Бесплатная модель GitHub

    def start(self):
        #Запуск браузера, переход на страницу, нажатие кнопки 'Начать задание'.
        logger.info("Запуск браузера...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",  # Маскировка автоматизации
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--use-gl=swiftshader",   # Эмуляция графики для headless
                "--disable-gpu",
                "--disable-software-rasterizer",
            ]
        )
        context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"}
        )
        self.page = context.new_page()
        # Скрипт для удаления признаков автоматизации
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        self.page.goto("https://sixmo.ru/")
        logger.info("Страница загружена")
        time.sleep(2)
        try:
            start_button = self.page.get_by_role("button", name="Начать задание")
            if start_button.count():
                start_button.click()
                logger.info("Нажата кнопка 'Начать задание'")
                self.page.wait_for_selector("input, select, textarea", timeout=30000)
                logger.info("Форма загружена (найдены поля)")
            else:
                logger.warning("Кнопка 'Начать задание' не найдена")
        except Exception as e:
            logger.warning(f"Не удалось нажать кнопку: {e}")

    def close(self):
        #Закрытие браузера и остановка Playwright.
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Браузер закрыт")

    def submit_form(self) -> str:
        #Основной метод: прохождение формы и возврат идентификатора.
        try:
            self.start()
            step_num = 1
            while True:
                logger.info(f"Обработка шага {step_num}")
                step_result = self.process_current_step()
                if step_result == "finished":
                    logger.info("Достигнут финальный шаг")
                    break
                self.click_next_button()
                time.sleep(5)  # Задержка для загрузки следующего шага
                step_num += 1
            identifier = self.extract_identifier()
            logger.info(f"Идентификатор получен: {identifier}")
            return identifier
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            if self.page:
                self.page.screenshot(path="error.png")
                logger.info("Скриншот сохранён как error.png")
            raise
        finally:
            self.close()

    def process_current_step(self) -> str:
        #Обрабатывает текущий шаг: ищет поля, заполняет их.
        try:
            # Ждём появления любых полей ввода (таймаут 30 сек)
            self.page.wait_for_selector("input, select, textarea, [type='file']", timeout=30000)
        except:
            if self.is_final_page():
                return "finished"
            else:
                logger.info("Поля не найдены, но это не финал")
                # В headless-режиме не делаем скриншот, чтобы избежать ошибок записи
                if not self.headless:
                    self.page.screenshot(path="debug_no_fields_after_click.png")
                return "finished"

        fields = self.find_input_fields()
        if not fields:
            return "finished"

        for field in fields:
            field_type = self.detect_field_type(field)
            logger.info(f"Поле: тип={field_type}")
            question_text = self.extract_question_text(field)

            if field_type == "select":
                # Ждём появления реальных опций (не плейсхолдера)
                try:
                    self.page.wait_for_function(
                        "select => Array.from(select.options).some(opt => opt.text !== 'Выберите вариант' && opt.value !== '')",
                        arg=field,
                        timeout=5000
                    )
                except:
                    pass

            options = self.extract_options(field) if field_type in ["radio", "select"] else None
            logger.info(f"Извлечены варианты: {options}")

            answer = self.generate_answer(question_text, field_type, options)
            logger.info(f"Ответ: {answer}")
            self.fill_field(field, answer, field_type)

        return "continue"

    def find_input_fields(self):
        #Находит все видимые поля ввода на странице.
        selectors = [
            "input:not([type='hidden'])",
            "select",
            "textarea",
            "[role='radiogroup']",
            "[role='combobox']",
            "[role='textbox']",
            "[data-testid]",
            "[name]",
            "[aria-label]",
            "div[contenteditable='true']"
        ]
        fields = []
        for selector in selectors:
            try:
                fields.extend(self.page.locator(selector).all())
            except:
                pass
        visible_fields = [f for f in fields if f.is_visible() and not f.is_disabled()]
        logger.info(f"Найдено полей: {len(visible_fields)}")
        return visible_fields

    def detect_field_type(self, field) -> str:
        #Определяет тип поля по тегу и атрибутам.
        tag = field.evaluate("el => el.tagName.toLowerCase()")
        if tag == "input":
            input_type = field.get_attribute("type")
            if input_type == "file":
                return "file"
            elif input_type in ["radio", "checkbox"]:
                return input_type
            else:
                return "text"
        elif tag == "select":
            return "select"
        elif tag == "textarea":
            return "textarea"
        elif field.get_attribute("role") == "radiogroup":
            return "radio"
        elif field.get_attribute("contenteditable") == "true":
            return "text"
        else:
            return "text"

    def extract_question_text(self, field) -> str:
        #Извлекает текст вопроса, связанного с полем, из DOM.
        text = field.evaluate("""
            el => {
                // Поиск label для id
                let id = el.id;
                if (id) {
                    let label = document.querySelector(`label[for="${id}"]`);
                    if (label && label.innerText.trim()) return label.innerText.trim();
                }
                // Поиск в родительском блоке
                let parent = el.closest('.field, .form-group, .block, .question-block, .question');
                if (parent) {
                    let textEl = parent.querySelector('.question, .label, .title, p, div:not(:has(input))');
                    if (textEl && textEl.innerText.trim()) return textEl.innerText.trim();
                }
                // Поиск предыдущего элемента (не интерактивного)
                let prev = el.previousElementSibling;
                while (prev) {
                    if (prev.innerText && prev.innerText.trim() && !prev.matches('input, select, button, textarea')) {
                        return prev.innerText.trim();
                    }
                    prev = prev.previousElementSibling;
                }
                let ariaLabel = el.getAttribute('aria-label');
                if (ariaLabel) return ariaLabel;
                return "Unknown question";
            }
        """)
        return text if text else "Unknown question"

    def extract_options(self, field) -> List[str]:
        #Извлекает варианты ответа для select или radio.
        tag_name = field.evaluate("el => el.tagName.toLowerCase()")
        if tag_name == "select":
            # Ждём появления реальных опций (не плейсхолдера)
            for attempt in range(5):
                options = field.evaluate("""
                    select => Array.from(select.options)
                        .filter(opt => opt.text !== 'Выберите вариант' && opt.value !== '')
                        .map(opt => opt.text.trim())
                """)
                if len(options) >= 1:
                    return options
                time.sleep(1)
            return []
        # Для радио-групп
        if field.get_attribute("role") == "radiogroup":
            radios = field.locator("input[type='radio']").all()
        else:
            parent = field.evaluate("el => el.closest('.field, .form-group, .block, div:has(> input[type=radio])')")
            if parent:
                radios = self.page.locator(f"{parent} input[type='radio']").all()
            else:
                radios = [field]
        texts = []
        for radio in radios:
            label = radio.evaluate("""
                el => {
                    let label = document.querySelector(`label[for="${el.id}"]`);
                    return label ? label.innerText.trim() : el.value;
                }
            """)
            if label:
                texts.append(label)
        return texts

    def generate_answer(self, question: str, field_type: str, options: List[str] = None) -> Any:
        #Генерирует ответ с помощью LLM или создаёт временный файл.
        if field_type == "select" and not options:
            return ""

        if options and field_type in ["select", "radio"]:
            prompt = f"""Ты — AI-агент, автоматически проходящий веб-форму.
Вопрос: {question}
Тип поля: {field_type}
Доступные варианты: {', '.join(options)}
Выбери один из этих вариантов и верни его текст в точности как он указан. Не добавляй пояснений."""
        else:
            if field_type == "file":
                prompt = f"""Для файла: верни 'FILE:' и затем одно слово из вселенной, связанной с вопросом.
Вопрос: {question}
Пример: если вопрос о Гарри Поттере, верни 'FILE: magic'.
Не добавляй пояснений."""
            else:
                prompt = f"""Ты — AI-агент, автоматически проходящий веб-форму.
Вопрос: {question}
Тип поля: {field_type}
Верни только короткий ответ (одно слово или короткую фразу), без пояснений. Не добавляй лишних слов."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=100
            )
            answer = response.choices[0].message.content.strip()

            if field_type == "file":
                if answer.startswith("FILE:"):
                    content = answer[5:].strip()
                else:
                    content = answer.split()[0] if answer else "magic"
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    f.write(content)
                    file_path = f.name
                logger.info(f"Создан файл: {file_path} с содержимым: {content}")
                return file_path
            else:
                # Пост-обработка ответа: удаление точки, укорачивание
                answer = answer.rstrip('.')
                if len(answer.split()) > 5:
                    words = answer.split()
                    if len(words[-1]) < 15:
                        answer = words[-1]
                    else:
                        answer = words[0]
                if options and field_type in ["select", "radio"]:
                    for opt in options:
                        if opt.strip() == answer:
                            return opt
                    for opt in options:
                        if answer in opt or opt in answer:
                            return opt
                    return options[0]
                return answer
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            if options:
                return options[0]
            if field_type == "file":
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    f.write("magic")
                    file_path = f.name
                return file_path
            return ""

    def fill_field(self, field, answer, field_type):
        #Заполняет поле ответом в зависимости от его типа.
        if field_type == "file":
            field.set_input_files(answer)
        elif field_type == "radio":
            radios = field.locator("input[type='radio']").all()
            if not radios:
                radios = [field]
            for radio in radios:
                label = radio.evaluate("""
                    el => {
                        let label = document.querySelector(`label[for="${el.id}"]`);
                        return label ? label.innerText.trim() : el.value;
                    }
                """)
                if label == answer:
                    radio.check()
                    break
        elif field_type == "select":
            options = field.evaluate("""
                select => Array.from(select.options)
                    .filter(opt => !opt.disabled)
                    .map(opt => ({ text: opt.text.trim(), value: opt.value }))
            """)
            logger.info(f"Available options: {options}")
            found = None
            if answer:
                for opt in options:
                    if opt['text'] == answer:
                        found = opt
                        break
                if not found:
                    for opt in options:
                        if answer in opt['text'] or opt['text'] in answer:
                            found = opt
                            break
            if found:
                field.select_option(value=found['value'])
                # Проверка успешности выбора
                selected_value = field.evaluate("select => select.value")
                if selected_value != found['value']:
                    field.select_option(value=found['value'])
                logger.info(f"Selected: {found['text']}")
            else:
                # Fallback: первый непустой вариант
                for opt in options:
                    if opt['text'] and opt['text'] != 'Выберите вариант':
                        field.select_option(value=opt['value'])
                        logger.warning(f"Fallback selected: {opt['text']}")
                        break
        elif field_type in ["text", "textarea"]:
            field.fill(answer)
        elif field_type == "checkbox":
            if answer.lower() in ["true", "yes", "да"]:
                field.check()
            else:
                field.uncheck()

    def click_next_button(self):
        #Находит и нажимает кнопку перехода к следующему шагу.
        possible_texts = ["Продолжить", "Далее", "Зафиксировать идентификатор", "Отправить"]
        for text in possible_texts:
            button = self.page.get_by_role("button", name=text)
            if button.count():
                button.first.click()
                self.page.wait_for_load_state("networkidle")
                time.sleep(1)
                logger.info(f"Нажата кнопка '{text}'")
                return

    def is_final_page(self) -> bool:
        #Проверяет, является ли текущая страница финальной (с идентификатором).
        if self.page.locator("text=ИДЕНТИФИКАТОР").count():
            return True
        if self.page.locator("text=Прохождение завершено").count():
            return True
        return False

    def extract_identifier(self) -> str:
        #Извлекает идентификатор из финальной страницы.
        id_element = self.page.locator("text=/[A-F0-9]{12}/")
        if id_element.count():
            return id_element.first.inner_text()
        text = self.page.inner_text("body")
        match = re.search(r"ИДЕНТИФИКАТОР\s+([A-F0-9]+)", text)
        if match:
            return match.group(1)
        return "Не удалось извлечь идентификатор"


def submit_form(headless: bool = False) -> str:
    #Функция для вызова как tool/skill. Возвращает идентификатор завершённой формы.
    agent = SixmoAutoAgent(headless=headless)
    return agent.submit_form()


if __name__ == "__main__":
    # Для Docker-запуска используем headless=True
    result = submit_form(headless=True)
    print(f"Результат: {result}")