//go:build !cgo

package sqlitedb

import "github.com/mattn/go-sqlite3"

// connectHook 在无 cgo 构建中不会被调用：go-sqlite3 的桩驱动打开连接时就返回“需要 cgo”的错误
func connectHook(*sqlite3.SQLiteConn) error { return nil }

// Available 报告本构建能否打开 SQLite（go-sqlite3 需要 cgo）
const Available = false
