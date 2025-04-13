#!/usr/bin/env python
"""
startup.py - このスクリプトはPCの起動／ログオン時に実行され、以下の処理を行います。
  1. 設定ファイル (config.ini) から必要なパラメータを読み込む。
  2. ネットワーク共有上のSQLiteデータベースが利用可能になるまで待機する。
  3. データベースに 'session_logs' テーブルが存在しない場合は自動作成する。
  4. 起動時の情報（PC固有ID、ログオンユーザー、起動時刻、曜日など）を取得する。
  5. 再試行ロジック付きで、直接データベースに起動ログレコードを挿入する。
  6. config.ini の "show_console" 設定に応じて、実行時のコンソールウィンドウを表示または非表示にする。
"""

import os
import socket
import datetime
import time
import configparser
import logging
import random
import ctypes

# 共通処理は utils.py に定義している
from utils import (
    load_config,
    ensure_table_exists,
    wait_for_network_share,
    get_startup_info,
    insert_startup_info_with_retry,
)

# ログ設定：ファイル出力やハンドラーの追加が必要な場合は適宜調整してください
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def hide_console():
    """
    Windows API を利用して、コンソールウィンドウを非表示にする。
    ※本番環境では画面表示を避け、バックグラウンドで起動ログの処理を行うために使用します。
    """
    whnd = ctypes.windll.kernel32.GetConsoleWindow()
    if whnd:
        # 0: SW_HIDE
        ctypes.windll.user32.ShowWindow(whnd, 0)

def main():
    # 設定ファイル (config.ini) の読み込み（UTF-8 エンコーディング）
    config = load_config("config.ini")

    # config.ini の設定に従い、コンソールウィンドウの表示/非表示を切り替え
    # "show_console" が False ならコンソールを非表示にする（デフォルトは True）
    show_console = config.getboolean("General", "show_console", fallback=True)
    if not show_console:
        hide_console()

    # 設定ファイルから各種パラメータを取得
    db_path = config.get("Database", "db_path")
    max_retries = config.getint("Retry", "max_retries")
    retry_interval = config.getfloat("Retry", "retry_interval")
    timeout = config.getfloat("General", "timeout")

    # ネットワーク共有上のDBファイルが利用可能になるまで待機する
    wait_for_network_share(db_path)

    # DBにテーブルが存在しない場合は自動で作成する
    ensure_table_exists(db_path, timeout)

    # 起動時の情報（pc_id, user_account, start_time, weekday等）を取得する
    startup_info = get_startup_info()
    logging.info("Startup info: %s", startup_info)

    # 再試行ロジックを用い、ネットワーク共有上のSQLite DBに起動ログを安全に挿入する
    insert_startup_info_with_retry(db_path, startup_info, max_retries, retry_interval, timeout)
    logging.info("Startup record inserted successfully.")

if __name__ == "__main__":
    main()
