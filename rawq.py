
import sys
import subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QProcess

class NeovimEmbed(QMainWindow):
    def __init__(self):
        super().__init__()

        # FIXME: esto viene de una notificación
        # self.setWindowTitle("Neovim en PyQt6")

        # FIXME: esto viene de armar la grilla del widget "canvas/texto", creo
        # self.setGeometry(100, 100, 800, 600)

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        # FIXME: es un textedit lo que queremos
        from PyQt6.QtWidgets import QTextEdit
        self.nvim_display = QTextEdit(self)
        self.nvim_display.setReadOnly(True)
        self.layout.addWidget(self.nvim_display)
        # FIXME: esto me parece que es overkill
        # painter = QPainter(self)
        # painter.setFont(self.font)

        # FIXME: dejar esto para entender que funciona
        self.button = QPushButton("Haz clic aquí", self)
        self.button.clicked.connect(self.test_action)
        self.layout.addWidget(self.button)

        # FIXME: eventualmente levantar nvim

    def test_action(self):
        print("Botón de PyQt6 presionado")
        # FIXME: acá poner algunas letras...
        # - en el origen y en otros lados
        # - con colores fg y bg
        # - unicode
        # - con tipografía! 'Hack Nerd Font Mono:h10.5'

    # FIXME: esto para escuchar teclas
    def keyPressEvent(self, event: QKeyEvent):
        """Evita que PyQt6 capture el teclado, permitiendo que Neovim lo maneje por completo."""
        event.ignore()  # FIXME: para qué?
        key = event.text()  # FIXME: eso qué da, y que le podemos pasar a nvim?
        print("========== Key", repr(key))

    # FIXME
    def closeEvent(self, event):
        """Cierra Neovim correctamente al cerrar la ventana."""
        print("============ close??")
        # if self.nvim_process.state() == QProcess.ProcessState.Running:
        #     self.nvim_process.terminate()
        #     self.nvim_process.waitForFinished(3000)
        # event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NeovimEmbed()
    window.show()
    sys.exit(app.exec())

