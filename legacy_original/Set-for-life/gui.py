import sys
import random
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel,
    QVBoxLayout, QSpinBox, QTextEdit
)
from PyQt5.QtGui import QFont


# --- Pattern Avoidance Rules for Set For Life ---
def generate_clean_sfl_line():
    # Avoid birthdays (1–31)
    pool = list(range(32, 48))  # 32–47 only

    # Avoid common lucky numbers
    avoid = {7, 9, 11, 13, 21}

    pool = [n for n in pool if n not in avoid]

    while True:
        line = sorted(random.sample(pool, 5))

        # Avoid sequences
        if any(line[i] + 1 == line[i+1] for i in range(4)):
            continue

        # Avoid multiples of 5 patterns
        if sum(1 for n in line if n % 5 == 0) >= 2:
            continue

        # Avoid symmetric patterns
        if len({n % 11 for n in line}) < 5:
            continue

        # Avoid tight clusters
        if max(line) - min(line) < 8:
            continue

        # Generate Life Ball (1–10), avoiding common picks
        life_avoid = {7}
        life_pool = [n for n in range(1, 11) if n not in life_avoid]
        life_ball = random.choice(life_pool)

        return line, life_ball


def generate_multiple_sfl_lines(count):
    return [generate_clean_sfl_line() for _ in range(count)]


# --- GUI Application ---
class SetForLifeGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Set For Life Generator (Pattern-Free)")
        self.setGeometry(200, 200, 450, 450)

        layout = QVBoxLayout()

        title = QLabel("Set For Life Number Generator")
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
        lines = generate_multiple_sfl_lines(count)

        text = "\n".join(f"{line} + Life Ball: {life}" for line, life in lines)
        self.output.setText(text)


# --- Run App ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = SetForLifeGUI()
    gui.show()
    sys.exit(app.exec_())
