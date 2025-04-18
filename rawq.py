
import sys

import asyncio
import qasync
from qasync import QEventLoop, QApplication
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPlainTextEdit
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor
import sys


class Vym(QMainWindow):
    def __init__(self):
        super().__init__()

        # XFIXME: esto viene de una notificación
        # self.setWindowTitle("Neovim en PyQt6")

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.display = QPlainTextEdit()
        self.display.setReadOnly(True)
        self.layout.addWidget(self.display)

        from PyQt6.QtGui import QFont
        font = QFont("Hack Nerd Font Mono", 11)  # Fuente y tamaño 'Hack Nerd Font Mono:h10.5'
        # XFIXME: lo de abajo puede no siempre andar, está bueno crear QFont con el valor redondeado por si lo ignora
        font.setPointSizeF(10.5)  # Usar tamaño decimal
        self.display.setFont(font)

        cols, rows = 80, 24  # Número de caracteres por línea y líneas visibles
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(font)
        char_width = fm.horizontalAdvance("M")  # Ancho de un carácter
        line_height = fm.height()  # Altura de una línea
        self.display.setFixedSize(char_width * cols, line_height * rows)

        # XFIXME: dejar esto para entender que funciona
        self.button = QPushButton("Haz clic aquí", self)
        self.button.clicked.connect(self.test_action)
        self.layout.addWidget(self.button)

        self.button2 = QPushButton("Run async task", self)
        self.button2.clicked.connect(lambda: asyncio.create_task(self.async_task()))
        self.layout.addWidget(self.button2)

        # XFIXME: eventualmente levantar nvim

    def test_action(self):
        print("Botón de PyQt6 presionado")

        # Insertar palabra al principio
        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText("Inicio!")

        # Texto inicial
        text = "Línea 1\nLínea 2\nLínea 3\nLínea 4"
        self.display.setPlainText(text)

        cursor.insertText(" Moño  昔 ばなし\n")
        cursor.insertText("emoji 😆d\n")
        cursor.insertText("emoji asd\n")

        # Mover a fila 3, columna 7
        # XFIXME: tenemos que entender mejor estos movimientos, y si son sobre el "view area" o el buffer en sí
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, 2)  # Bajar dos líneas
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, 6) # Mover a col 7

        # Formatear palabra con fondo amarillo y texto azul
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("blue"))
        fmt.setBackground(QColor("yellow"))

        cursor.insertText("Marcado", fmt)

    async def async_task(self):
        for i in range(5):
            print(f"Async iteration {i}")
            await asyncio.sleep(1)  # No bloquea el event loop de Qt

    def keyPressEvent(self, event: QKeyEvent):
        """Evita que PyQt6 capture el teclado, permitiendo que Neovim lo maneje por completo."""
        event.ignore()  # XFIXME: para qué?
        key = event.text()  # XFIXME: eso qué da, y que le podemos pasar a nvim?
        print("========== Key", repr(key))
        # XFIXME: es raro; el Tab no se ve, y los modificadores a veces vienen o no

    def closeEvent(self, event):
        """Cierra Neovim correctamente al cerrar la ventana."""
        print("============ close??")
        # XFIXME: cerrar el proceso de nvim
        # if self.nvim_process.state() == QProcess.ProcessState.Running:
        #     self.nvim_process.terminate()
        #     self.nvim_process.waitForFinished(3000)
        # event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    main_window = Vym()
    main_window.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
