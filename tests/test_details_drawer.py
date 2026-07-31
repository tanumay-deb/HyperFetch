import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import task as T
from gui2.details_drawer import DetailsDrawer


def test_opening_drawer_populates_structured_headers_without_crashing():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    task = T.DownloadTask(
        "https://example.test/file.zip",
        "C:/Downloads/file.zip",
        headers={"Referer": "https://example.test/"},
    )
    task.status = T.DOWNLOADING

    drawer.open_for(task)
    app.processEvents()

    assert drawer.h_table.rowCount() == 1
    assert drawer.h_table.item(0, 0).text() == "Referer"
    assert any("Downloading" in line for line in drawer._log_lines)
