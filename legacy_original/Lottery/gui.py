import sys
import random
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel,
    QVBoxLayout, QSpinBox, QTextEdit
)
from PyQt5.QtGui import QFont


# --- Pattern Avoidance Rules ---
def generate_clean_line():
    # Avoid birthdays (1–31)
    pool = list(range(32, 60))

    # Avoid common lucky numbers
    avoid = {7, 9, 11, 13, 21}

    # Remove avoid numbers if they appear in pool
    pool = [n for n in pool if n not in avoid]

    while True:
        line = sorted(random.sample(pool, 6))

        # Avoid sequences (e.g., 41,42,43)
        if any(line[i] + 1 == line[i+1] for i in range(5)):
            continue

        # Avoid multiples of 5 patterns
        if sum(1 for n in line if n % 5 == 0) >= 3:
            continue

        # Avoid symmetric patterns (e.g., 33,44,55)
        if len({n % 11 for n in line}) < 6:
            continue

        # Avoid tight clusters (spread < 10)
        if max(line) - min(line) < 10:
            continue

        return line


def generate_multiple_lines(count):
    return [generate_clean_line() for _ in range(count)]


# --- GUI Application ---
class LottoGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Low-Duplication Lotto Generator")
        self.setGeometry(200, 200, 420, 420)

        layout = QVBoxLayout()

        title = QLabel("Lotto Number Generator (Pattern-Free)")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Generates lines that avoid all common human patterns")
        subtitle.setFont(QFont("Arial", 10))
        layout.addWidget(subtitle)

        # Spin box for number of lines
        self.spin = QSpinBox()
        self.spin.setRange(1, 20)
        self.spin.setValue(5)
        layout.addWidget(self.spin)

        # Generate button
        self.button = QPushButton("Generate Lines")
        self.button.setFont(QFont("Arial", 12))
        self.button.clicked.connect(self.generate)
        layout.addWidget(self.button)

        # Output box
        self.output = QTextEdit()
        self.output.setFont(QFont("Courier", 12))
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

        self.setLayout(layout)

    def generate(self):
        count = self.spin.value()
        lines = generate_multiple_lines(count)

        text = "\n".join(str(line) for line in lines)
        self.output.setText(text)


# --- Run App ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = LottoGUI()
    gui.show()
    sys.exit(app.exec_())
