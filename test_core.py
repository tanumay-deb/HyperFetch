import os
import time
import threading
from task import DownloadTask, COMPLETED
from downloader import Downloader

def test_core():
    url = "http://speedtest.tele2.net/1MB.zip"
    save_path = os.path.abspath("test_1MB.zip")
    temp_path = save_path + ".hfdownload"
    
    # Cleanup before test
    if os.path.exists(save_path):
        os.remove(save_path)
    if os.path.exists(temp_path):
        os.remove(temp_path)

    print("Instantiating DownloadTask...")
    task = DownloadTask(url, save_path)
    
    print("Setting speed limit to 500 KB/s...")
    task.set_speed_limit(500 * 1024)
    
    print("Instantiating Downloader...")
    dl = Downloader(task)
    
    print("Running downloader in a background thread to check for .hfdownload file...")
    dl_thread = threading.Thread(target=dl.run)
    dl_thread.start()
    
    # Assert .hfdownload file is created during download
    hf_created = False
    for _ in range(50):
        if os.path.exists(temp_path):
            hf_created = True
            break
        time.sleep(0.1)
        
    try:
        assert hf_created, "The .hfdownload file was not created during the download."
        print("Assertion passed: .hfdownload file was created.")
    except AssertionError as e:
        task.request_cancel()
        dl_thread.join()
        raise e
        
    print("Waiting for download to finish...")
    dl_thread.join(timeout=15)
    
    if dl_thread.is_alive():
        print("Download is taking too long. Cancelling...")
        task.request_cancel()
        dl_thread.join()
        raise AssertionError("Download timed out.")
        
    assert task.status == COMPLETED, f"Download status is not COMPLETED, it is {task.status}. Error: {task.error}"
    assert os.path.exists(save_path), "The final downloaded file was not found."
    
    # Cleanup after test
    if os.path.exists(save_path):
        os.remove(save_path)
        
    print("Test passed successfully!")

if __name__ == "__main__":
    test_core()
