#!/usr/bin/env python
"""
utils.py - startup.py と shutdown.py で共通に利用するユーティリティ関数群

提供する機能:
  ・load_config: 設定ファイル (config.ini) の読み込み（UTF-8）
  ・ensure_table_exists: 'session_logs' テーブルの存在確認・自動作成
  ・wait_for_network_share: ネットワーク共有上のSQLiteファイルが利用可能になるまで待機
  ・compute_duration: 起動時刻とシャットダウン時刻から利用時間（秒）を計算（負の場合は0）
  ・execute_db_write: 汎用DB書き込み関数（再試行ロジック付き）
  ・get_start_time_for_duration: 指定PC・ユーザーの未更新起動レコードから start_time を取得
  ・get_startup_info: 起動／ログオン時の情報を収集（shutdown_timeは空、duration=0）
  ・insert_startup_record: 起動情報のレコードをDBに挿入する
  ・insert_startup_info_with_retry: 起動情報の挿入を再試行ロジック付きで実行する
  ・update_shutdown_record: シャットダウン時の情報で、未更新の起動レコードを更新する
  ・insert_shutdown_record: 対象がない場合にシャットダウン情報を新規挿入する
  ・update_shutdown_info_with_retry: シャットダウン情報の更新／挿入を再試行ロジック付きで実行する
"""

import configparser
import os
import sqlite3
import time
import logging
import random
import datetime
import socket

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


def load_config(config_path="config.ini"):
    """
    設定ファイル config.ini を UTF-8 エンコーディングで読み込み、設定オブジェクトを返す。

    Raises:
        FileNotFoundError: 指定ファイルが存在しない場合
    Returns:
        configparser.ConfigParser: 読み込んだ設定オブジェクト
    """
    config = configparser.ConfigParser()
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file '{config_path}' not found.")
    with open(config_path, "r", encoding="utf-8") as f:
        config.read_file(f)
    return config


def ensure_table_exists(db_path, timeout):
    """
    SQLiteデータベース内に 'session_logs' テーブルが存在しない場合、
    CREATE TABLE IF NOT EXISTS を実行してテーブルを自動作成する。

    Parameters:
        db_path (str): データベースファイルのパス
        timeout (float): SQLite接続時のタイムアウト秒数
    """
    create_table_query = """
    CREATE TABLE IF NOT EXISTS session_logs (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        pc_id TEXT NOT NULL,
        user_account TEXT NOT NULL,
        start_time DATETIME NOT NULL,
        shutdown_time DATETIME,
        duration INTEGER,
        session_type TEXT,
        weekday TEXT
    );
    """
    try:
        with sqlite3.connect(db_path, timeout=timeout) as conn:
            cursor = conn.cursor()
            cursor.execute(create_table_query)
            conn.commit()
            logging.info("Ensured that table 'session_logs' exists in the database.")
    except sqlite3.Error as e:
        logging.error("Error ensuring table exists: %s", e)
        raise


def wait_for_network_share(db_path, wait_interval=5, max_wait=60):
    """
    ネットワーク共有上のSQLiteファイル (db_path) が利用可能になるまで待機する。

    Parameters:
        db_path (str): ネットワーク共有上のSQLiteデータベースファイルのパス（UNCパス等）
        wait_interval (int): 存在チェックの間隔（秒）
        max_wait (int): 最大待機時間（秒）

    Raises:
        Exception: 指定された最大待機時間内に利用可能にならなかった場合
    """
    waited = 0
    while not os.path.exists(db_path):
        logging.warning("Network share %s not available. Waiting %d seconds...", db_path, wait_interval)
        time.sleep(wait_interval)
        waited += wait_interval
        if waited >= max_wait:
            raise Exception(f"Network share {db_path} is not available after waiting {max_wait} seconds.")
    logging.info("Network share %s is available.", db_path)


def compute_duration(start_time_str, shutdown_time_str):
    """
    起動時刻 (start_time_str) とシャットダウン時刻 (shutdown_time_str) の差分から利用時間 (秒) を算出する。
    負の値の場合は 0 を返す。

    Parameters:
        start_time_str (str): 起動時刻 (YYYY-MM-DD HH:MM:SS)
        shutdown_time_str (str): シャットダウン時刻 (YYYY-MM-DD HH:MM:SS)

    Returns:
        int: 利用時間（秒）
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        start_time = datetime.datetime.strptime(start_time_str, fmt)
        shutdown_time = datetime.datetime.strptime(shutdown_time_str, fmt)
        duration = (shutdown_time - start_time).total_seconds()
        if duration < 0:
            logging.warning("Computed duration is negative; setting duration to 0.")
            return 0
        return int(duration)
    except Exception as e:
        logging.error("Error computing duration: %s", e)
        return 0


def execute_db_write(query, params, db_path, timeout, max_retries, retry_interval):
    """
    SQLiteデータベースに対して指定された SQL クエリを実行する汎用関数。
    "database is locked" エラー発生時、指定された再試行回数までランダムジッター付きで再試行します。

    Parameters:
        query (str): 実行するSQLクエリ（INSERT、UPDATE、DELETE など）
        params (tuple): SQLクエリに渡すパラメータ
        db_path (str): データベースファイルのパス
        timeout (float): SQLite接続時のタイムアウト秒数
        max_retries (int): 最大再試行回数
        retry_interval (float): 再試行の基本間隔（秒）

    Raises:
        sqlite3.OperationalError: 再試行回数を超えた場合に例外をスロー

    Returns:
        None
    """
    attempt = 0
    while True:
        try:
            with sqlite3.connect(db_path, timeout=timeout) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
            logging.info("Database write successful on attempt %d.", attempt + 1)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                attempt += 1
                if attempt > max_retries:
                    logging.error("Max retries reached (%d). Unable to execute write query.", max_retries)
                    raise e
                else:
                    sleep_time = retry_interval * random.uniform(1.0, 1.5)
                    logging.warning("Database is locked. Retry attempt %d/%d in %.2f seconds...", attempt, max_retries, sleep_time)
                    time.sleep(sleep_time)
            else:
                logging.error("OperationalError during DB write: %s", e)
                raise e


def get_start_time_for_duration(db_path, pc_id, user_account, timeout):
    """
    指定された pc_id と user_account に対して、shutdown_time が未設定の最新の起動レコードから
    start_time を取得する。該当レコードがなければ None を返す。

    Parameters:
        db_path (str): データベースファイルのパス
        pc_id (str): PCのホスト名
        user_account (str): ログオンユーザー名
        timeout (float): SQLite接続時のタイムアウト秒数

    Returns:
        str or None: 取得された起動時刻 (YYYY-MM-DD HH:MM:SS) または None
    """
    with sqlite3.connect(db_path, timeout=timeout) as conn:
        cursor = conn.cursor()
        query = """
        SELECT start_time FROM session_logs
        WHERE pc_id = ? AND user_account = ? AND (shutdown_time IS NULL OR shutdown_time = '')
        ORDER BY start_time DESC LIMIT 1
        """
        cursor.execute(query, (pc_id, user_account))
        result = cursor.fetchone()
        return result[0] if result else None


# --------------- Startup Functions ---------------
def get_startup_info():
    """
    起動／ログオン時に必要な情報を収集し、辞書形式で返す。

    取得項目:
      - pc_id       : ホスト名（PC固有ID）
      - user_account: ログオンユーザー名
      - start_time  : 起動時刻（YYYY-MM-DD HH:MM:SS形式）
      - weekday     : 起動時の曜日（例："Mon"）
      - shutdown_time: 起動時は未設定（空文字）
      - session_type: "normal"（初期値）
      - duration    : 0（初期値）

    Returns:
        dict: 取得された起動情報
    """
    data = {}
    data['pc_id'] = socket.gethostname()
    data['user_account'] = os.environ.get("USERNAME", "unknown")
    now = datetime.datetime.now()
    data['start_time'] = now.strftime("%Y-%m-%d %H:%M:%S")
    data['weekday'] = now.strftime("%a")
    data['shutdown_time'] = ""
    data['session_type'] = "normal"
    data['duration'] = 0
    return data


def insert_startup_record(db_path, record_data, timeout):
    """
    指定された SQLite データベースに接続し、起動情報の新規レコードを挿入する。

    Parameters:
        db_path (str): データベースファイルのパス
        record_data (dict): 起動情報
        timeout (float): SQLite接続時のタイムアウト秒数

    Returns:
        None
    """
    with sqlite3.connect(db_path, timeout=timeout) as conn:
        cursor = conn.cursor()
        query = """
            INSERT INTO session_logs
                (pc_id, user_account, start_time, shutdown_time, duration, session_type, weekday)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(query, (
            record_data['pc_id'],
            record_data['user_account'],
            record_data['start_time'],
            record_data['shutdown_time'],
            record_data['duration'],
            record_data['session_type'],
            record_data['weekday']
        ))
        conn.commit()


def insert_startup_info_with_retry(db_path, record_data, max_retries, retry_interval, timeout):
    """
    SQLiteへの書き込み時に「database is locked」エラーが発生した場合、
    指定された再試行回数と再試行間隔で起動情報の新規レコード挿入を再試行する。

    Parameters:
        db_path (str): SQLiteデータベースファイルのパス
        record_data (dict): 挿入する起動情報
        max_retries (int): 最大再試行回数
        retry_interval (float): 再試行の基本間隔（秒）
        timeout (float): SQLite接続時のタイムアウト秒数

    Raises:
        sqlite3.OperationalError: 再試行回数超過時にスロー

    Returns:
        None
    """
    attempt = 0
    while True:
        try:
            insert_startup_record(db_path, record_data, timeout)
            logging.info("Startup record inserted successfully on attempt %d.", attempt + 1)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                attempt += 1
                if attempt > max_retries:
                    logging.error("Max retries reached (%d). Unable to insert startup record.", max_retries)
                    raise e
                else:
                    sleep_time = retry_interval * random.uniform(1.0, 1.5)
                    logging.warning("Database is locked. Retry attempt %d/%d in %.2f seconds...", attempt, max_retries, sleep_time)
                    time.sleep(sleep_time)
            else:
                logging.error("OperationalError during startup DB insert: %s", e)
                raise e


# --------------- Shutdown Functions ---------------
def get_shutdown_info():
    """
    シャットダウン／中断時の情報を収集し、辞書形式で返す。

    取得項目:
      - pc_id       : ホスト名（PC固有ID）
      - user_account: ログオンユーザー名
      - shutdown_time: シャットダウン時刻（YYYY-MM-DD HH:MM:SS形式）
      - weekday     : シャットダウン時の曜日（例："Mon"）
      - session_type: "normal"（初期値）
      - duration    : 0（初期値、後で計算）

    Returns:
        dict: 取得されたシャットダウン情報
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


def update_shutdown_record(db_path, record_data, timeout):
    """
    指定されたデータベースに接続し、対象PC・ユーザーの最新の起動レコードで
    shutdown_time が未設定のものを更新する。更新項目は shutdown_time, session_type, duration, weekday。

    Parameters:
        db_path (str): データベースファイルのパス
        record_data (dict): シャットダウン情報
        timeout (float): SQLite接続時のタイムアウト秒数

    Returns:
        int: 更新された行数
    """
    with sqlite3.connect(db_path, timeout=timeout) as conn:
        cursor = conn.cursor()
        query = """
        UPDATE session_logs
        SET shutdown_time = ?, session_type = ?, duration = ?, weekday = ?
        WHERE pc_id = ? AND user_account = ? AND (shutdown_time IS NULL OR shutdown_time = '')
        """
        cursor.execute(query, (
            record_data['shutdown_time'],
            record_data['session_type'],
            record_data['duration'],
            record_data['weekday'],
            record_data['pc_id'],
            record_data['user_account']
        ))
        conn.commit()
        return cursor.rowcount


def insert_shutdown_record(db_path, record_data, timeout):
    """
    対象の起動レコードが存在しなかった場合、新規にレコードを挿入する。
    この場合、起動時刻が不明なため、shutdown_time を start_time の代替として保存する。

    Parameters:
        db_path (str): データベースファイルのパス
        record_data (dict): シャットダウン情報
        timeout (float): SQLite接続時のタイムアウト秒数

    Returns:
        None
    """
    with sqlite3.connect(db_path, timeout=timeout) as conn:
        cursor = conn.cursor()
        query = """
        INSERT INTO session_logs (pc_id, user_account, start_time, shutdown_time, duration, session_type, weekday)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(query, (
            record_data['pc_id'],
            record_data['user_account'],
            record_data['shutdown_time'],  # 起動時刻情報がないため仮に shutdown_time を利用
            record_data['shutdown_time'],
            record_data['duration'],
            record_data['session_type'],
            record_data['weekday']
        ))
        conn.commit()


def update_shutdown_info_with_retry(db_path, record_data, max_retries, retry_interval, timeout):
    """
    SQLiteへの書き込み時に「database is locked」エラーが発生した場合、
    指定された再試行回数および再試行間隔で再試行する。ランダムジッター付き。

    Parameters:
        db_path (str): SQLiteデータベースファイルのパス
        record_data (dict): 更新するシャットダウン情報
        max_retries (int): 最大再試行回数
        retry_interval (float): 再試行基本間隔（秒）
        timeout (float): SQLite接続時のタイムアウト秒数

    Raises:
        sqlite3.OperationalError: 再試行回数超過時にエラーをスロー

    Returns:
        None
    """
    attempt = 0
    while True:
        try:
            affected = update_shutdown_record(db_path, record_data, timeout)
            if affected == 0:
                insert_shutdown_record(db_path, record_data, timeout)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                attempt += 1
                if attempt > max_retries:
                    logging.error("Max retries reached (%d). Unable to update shutdown record.", max_retries)
                    raise e
                else:
                    sleep_time = retry_interval * random.uniform(1.0, 1.5)
                    logging.warning("Database is locked. Retry attempt %d/%d in %.2f seconds...", attempt, max_retries, sleep_time)
                    time.sleep(sleep_time)
            else:
                logging.error("OperationalError during DB update: %s", e)
                raise e


# ---------------------------
# テスト用使用例（個別実行時のテスト用）
# ---------------------------
if __name__ == "__main__":
    import socket
    import datetime

    config = load_config("config.ini")
    db_path = config.get("Database", "db_path")
    max_retries = config.getint("Retry", "max_retries")
    retry_interval = config.getfloat("Retry", "retry_interval")
    timeout = config.getfloat("General", "timeout")

    # シャットダウン情報のサンプルを生成
    shutdown_info = {
        "pc_id": socket.gethostname(),
        "user_account": os.environ.get("USERNAME", "unknown"),
        "shutdown_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": datetime.datetime.now().strftime("%a"),
        "session_type": "normal",
        "duration": 0
    }

    # 起動レコードから start_time を取得し、duration を算出する
    start_time_str = get_start_time_for_duration(db_path, shutdown_info['pc_id'], shutdown_info['user_account'], timeout)
    if start_time_str:
        shutdown_info['duration'] = compute_duration(start_time_str, shutdown_info['shutdown_time'])
    else:
        shutdown_info['duration'] = 0

    logging.info("Shutdown info: %s", shutdown_info)
    update_shutdown_info_with_retry(db_path, shutdown_info, max_retries, retry_interval, timeout)
    logging.info("Shutdown record updated successfully.")
