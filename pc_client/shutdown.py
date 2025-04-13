#!/usr/bin/env python
"""
shutdown.py - このスクリプトはPCのシャットダウン／中断時に実行され、
既存の起動レコードに対して shutdown_time および duration を更新します。
"""

import os
import socket
import datetime
import configparser
import logging

# 共通処理を utils.py からインポート
from utils import (
    load_config,
    ensure_table_exists,
    wait_for_network_share,
    get_start_time_for_duration,
    compute_duration,
    update_shutdown_info_with_retry,
)

# ログ設定（必要に応じて handlers を追加してください）
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def get_shutdown_info():
    """
    シャットダウン／中断時に必要な情報を取得し、辞書形式で返す。

    取得項目:
      - pc_id       : ホスト名（PC固有ID）
      - user_account: ログオンユーザー名（環境変数 "USERNAME" から取得）
      - shutdown_time: 現在のシャットダウン時刻（YYYY-MM-DD HH:MM:SS 形式）
      - weekday     : シャットダウン時の曜日（短縮形、例："Mon"）
      - session_type: 初期値 "normal"（正常なシャットダウンの場合）
      - duration    : 起動時刻との差分で算出される利用時間（初期値 0）
    """
    data = {}
    data['pc_id'] = socket.gethostname()
    data['user_account'] = os.environ.get("USERNAME", "unknown")
    now = datetime.datetime.now()
    data['shutdown_time'] = now.strftime("%Y-%m-%d %H:%M:%S")
    data['weekday'] = now.strftime("%a")
    data['session_type'] = "normal"
    data['duration'] = 0
    return data

def main():
    """
    main() 関数の処理内容:
      1. config.ini から設定ファイルを UTF-8 で読み込み、db_path、再試行設定、タイムアウトなどを取得する。
      2. ネットワーク共有上の SQLite データベースが利用可能になるまで待機する。
      3. DB内に 'session_logs' テーブルが存在しない場合は自動で作成する。
      4. シャットダウン時の情報を収集する。
      5. 対象の起動レコードから start_time を取得し、compute_duration で利用時間を算出する。
      6. update_shutdown_info_with_retry() により、再試行付きでデータベースの起動レコードを更新する。
    """
    # 設定ファイルからパラメータ読み込み（UTF-8エンコーディング）
    config = load_config("config.ini")
    db_path = config.get("Database", "db_path")
    max_retries = config.getint("Retry", "max_retries")
    retry_interval = config.getfloat("Retry", "retry_interval")
    timeout = config.getfloat("General", "timeout")

    # ネットワーク共有上のDBが利用可能になるまで待機する
    wait_for_network_share(db_path)

    # テーブルが存在しない場合は自動で作成する
    ensure_table_exists(db_path, timeout)

    # シャットダウン時の情報を取得
    shutdown_info = get_shutdown_info()

    # 起動レコードから start_time を取得し、duration を算出する
    start_time_str = get_start_time_for_duration(db_path, shutdown_info['pc_id'], shutdown_info['user_account'], timeout)
    if start_time_str:
        shutdown_info['duration'] = compute_duration(start_time_str, shutdown_info['shutdown_time'])
    else:
        shutdown_info['duration'] = 0

    logging.info("Shutdown info: %s", shutdown_info)

    # 再試行ロジック付きでシャットダウン情報をDBに更新する
    update_shutdown_info_with_retry(db_path, shutdown_info, max_retries, retry_interval, timeout)
    logging.info("Shutdown record updated successfully.")

if __name__ == "__main__":
    main()
