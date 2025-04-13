#!/usr/bin/env python
import sys
import os
import logging
import datetime

# EXE化された場合、sys.frozen が True となるので、EXEのあるディレクトリを基準にする
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
# カレントディレクトリを設定
os.chdir(base_dir)

# ログファイルを、EXE と同じディレクトリ内の "test_shutdown.log" に出力するよう設定
log_path = os.path.join(base_dir, "test_shutdown.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    filename=log_path,
    filemode='a'
)

def main():
    # 現在のタイムスタンプを付与したメッセージをログに出力
    logging.info("Test shutdown script executed. Timestamp: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

if __name__ == "__main__":
    main()
