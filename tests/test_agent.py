import pytest
import tempfile
import os
from unittest.mock import MagicMock, patch, mock_open, call
from agent import SixmoAutoAgent
from playwright.sync_api import Page, Locator

# Фикстура для создания экземпляра агента без инициализации браузера
@pytest.fixture
def agent():
    a = SixmoAutoAgent(headless=True)
    a.client = MagicMock()
    return a

# Фикстура для мока поля (locator)
@pytest.fixture
def mock_field():
    field = MagicMock(spec=Locator)
    field.is_visible.return_value = True
    field.is_disabled.return_value = False
    return field

# Тесты для detect_field_type
class TestDetectFieldType:
    def test_input_text(self, agent, mock_field):
        mock_field.evaluate.return_value = "input"
        mock_field.get_attribute.return_value = "text"
        assert agent.detect_field_type(mock_field) == "text"

    def test_input_file(self, agent, mock_field):
        mock_field.evaluate.return_value = "input"
        mock_field.get_attribute.return_value = "file"
        assert agent.detect_field_type(mock_field) == "file"

    def test_input_radio(self, agent, mock_field):
        mock_field.evaluate.return_value = "input"
        mock_field.get_attribute.return_value = "radio"
        assert agent.detect_field_type(mock_field) == "radio"

    def test_select(self, agent, mock_field):
        mock_field.evaluate.return_value = "select"
        assert agent.detect_field_type(mock_field) == "select"

    def test_textarea(self, agent, mock_field):
        mock_field.evaluate.return_value = "textarea"
        assert agent.detect_field_type(mock_field) == "textarea"

    def test_radiogroup(self, agent, mock_field):
        mock_field.evaluate.return_value = "div"
        mock_field.get_attribute.return_value = "radiogroup"
        assert agent.detect_field_type(mock_field) == "radio"

# Тесты для extract_question_text
class TestExtractQuestionText:
    def test_by_label(self, agent, mock_field):
        mock_field.evaluate.return_value = "Question text"
        result = agent.extract_question_text(mock_field)
        assert result == "Question text"

# Тесты для extract_options
class TestExtractOptions:
    def test_select_with_options(self, agent, mock_field):
        # Используем лямбду для последовательных вызовов evaluate
        mock_field.evaluate.side_effect = lambda *args, **kwargs: "select" if mock_field.evaluate.call_count == 1 else ["Option1", "Option2"]
        mock_field.get_attribute.return_value = "SELECT"
        options = agent.extract_options(mock_field)
        assert options == ["Option1", "Option2"]

    def test_select_no_options(self, agent, mock_field):
        mock_field.evaluate.side_effect = lambda *args, **kwargs: "select" if mock_field.evaluate.call_count == 1 else []
        mock_field.get_attribute.return_value = "SELECT"
        options = agent.extract_options(mock_field)
        assert options == []

    def test_radio_group(self, agent, mock_field):
        radio1 = MagicMock()
        radio2 = MagicMock()
        radio1.evaluate.return_value = "Radio1"
        radio2.evaluate.return_value = "Radio2"
        mock_field.get_attribute.return_value = "radiogroup"
        mock_field.locator.return_value.all.return_value = [radio1, radio2]
        options = agent.extract_options(mock_field)
        assert options == ["Radio1", "Radio2"]

# Тесты для generate_answer
class TestGenerateAnswer:
    def test_text_answer(self, agent):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Expected answer"
        agent.client.chat.completions.create.return_value = mock_response

        answer = agent.generate_answer("What is your name?", "text")
        assert answer == "Expected answer"

    def test_file_answer(self, agent):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "FILE: some content"
        agent.client.chat.completions.create.return_value = mock_response

        with patch("tempfile.NamedTemporaryFile") as mock_temp:
            mock_file = MagicMock()
            mock_file.name = "/tmp/test.txt"
            mock_temp.return_value.__enter__.return_value = mock_file
            answer = agent.generate_answer("Provide a file", "file")
            assert answer == "/tmp/test.txt"
            mock_file.write.assert_called_once_with("some content")

    def test_select_with_options(self, agent):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Option2"
        agent.client.chat.completions.create.return_value = mock_response

        answer = agent.generate_answer("Choose", "select", ["Option1", "Option2", "Option3"])
        assert answer == "Option2"

    def test_select_with_fallback(self, agent):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Invalid"
        agent.client.chat.completions.create.return_value = mock_response

        answer = agent.generate_answer("Choose", "select", ["Option1", "Option2"])
        assert answer == "Option1"

# Тесты для fill_field
class TestFillField:
    def test_fill_text(self, agent, mock_field):
        agent.fill_field(mock_field, "Hello", "text")
        mock_field.fill.assert_called_once_with("Hello")

    def test_fill_file(self, agent, mock_field):
        agent.fill_field(mock_field, "/path/to/file.txt", "file")
        mock_field.set_input_files.assert_called_once_with("/path/to/file.txt")

    def test_select_option(self, agent, mock_field):
        mock_field.evaluate.return_value = [
            {"text": "Option1", "value": "val1"},
            {"text": "Option2", "value": "val2"}
        ]
        agent.fill_field(mock_field, "Option2", "select")
        # В реализации select_option вызывается дважды: выбор и проверка
        expected_calls = [call(value="val2"), call(value="val2")]
        mock_field.select_option.assert_has_calls(expected_calls)
        assert mock_field.select_option.call_count == 2

# Тест для полного прохождения
def test_submit_form_integration(agent):
    mock_page = MagicMock(spec=Page)
    agent.page = mock_page

    agent.is_final_page = MagicMock(side_effect=[False, False, True])
    agent.process_current_step = MagicMock(side_effect=["continue", "continue", "finished"])
    agent.click_next_button = MagicMock()
    agent.extract_identifier = MagicMock(return_value="TEST_ID")

    agent.start = MagicMock()
    agent.close = MagicMock()

    result = agent.submit_form()
    assert result == "TEST_ID"
    assert agent.process_current_step.call_count == 3
    assert agent.click_next_button.call_count == 2