//go:build cgo

package sqlitedb

import "github.com/mattn/go-sqlite3"

// connectHook 在每个新连接上执行 connectPragmas
func connectHook(conn *sqlite3.SQLiteConn) error {
	_, err := conn.Exec(connectPragmas, nil)
	return err
}

// Available 报告本构建能否打开 SQLite（go-sqlite3 需要 cgo）
const Available = true
