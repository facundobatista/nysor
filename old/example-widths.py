from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QMainWindow, QSizePolicy
)
# from .PyQt6.QtGui import QPainter, QFont, QTextLayout
# from 昔PyQt6.QtCore import QPointF, Qt XYZ
# from ✖PyQt6.QtWidgets import QScrollArea

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QFont, QTextLayout, QMouseEvent, QColor
from PyQt6.QtCore import QPoint, Qt, QRect

CELL_WIDTH = 10
CELL_HEIGHT = 20


class TextViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.font = QFont("Courier", 12)
        self.lines = [
            ("Hello, world!", 0, 0),
            ("Second line here", 2, 5),
            ("Aligned to the right!", 4, 20),
        ]
        self.selection_start = None  # (row, col)
        self.selection_end = None

    def paintEvent(self, event):
        print("==== paint")
        painter = QPainter(self)
        painter.setFont(self.font)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)

        # Dibujar texto + fondo de selección si hay
        for idx, (text, row, col) in enumerate(self.lines):
            layout = QTextLayout(text, self.font)
            layout.beginLayout()
            line = layout.createLine()
            layout.endLayout()

            if not line.isValid():
                continue

            y = row * CELL_HEIGHT
            for i, char in enumerate(text):
                x = (col + i) * CELL_WIDTH
                selected = self._is_selected(row, col + i)
                if selected:
                    painter.fillRect(QRect(x, y, CELL_WIDTH, CELL_HEIGHT), QColor("#cce5ff"))
                painter.drawText(x, y + CELL_HEIGHT - 4, char)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        self.selection_start = self._pos_to_cell(event.pos())
        self.selection_end = self.selection_start
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.selection_start:
            self.selection_end = self._pos_to_cell(event.pos())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.selection_start:
            self.selection_end = self._pos_to_cell(event.pos())
            self.update()

    def _pos_to_cell(self, pos: QPoint):
        return (pos.y() // CELL_HEIGHT, pos.x() // CELL_WIDTH)

    def _is_selected(self, row, col):
        if not self.selection_start or not self.selection_end:
            return False

        (r1, c1) = self.selection_start
        (r2, c2) = self.selection_end

        # Normalizar
        top = min(r1, r2)
        bottom = max(r1, r2)
        left = min(c1, c2)
        right = max(c1, c2)

        return top <= row <= bottom and left <= col <= right


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Text Viewer App")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        # --- Viewer con scroll ---
        self.viewer = TextViewer()
        self.viewer.setMinimumSize(600, 400)  # tamaño de contenido base

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.viewer)

        main_layout.addWidget(scroll)

        # --- Botones abajo ---
        button_layout = QHBoxLayout()
        btn1 = QPushButton("Botón 1")
        btn2 = QPushButton("Botón 2")
        button_layout.addWidget(btn1)
        button_layout.addWidget(btn2)

        button_container = QWidget()
        button_container.setLayout(button_layout)
        button_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(button_container)




if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.resize(600, 400)
    window.show()
    app.exec()

