//go:build !cgo || nomozjs

// 无 cgo 构建（CI 单元测试、go vet、交叉编译检查）用的桩：类型与 cgo 版一致，
// 需要 SpiderMonkey 的调用都返回 ErrUnavailable。生产构建（CGO_ENABLED=1、不带 nomozjs/nolibwebp）不编译本文件。
package mozjs

import (
	"bytes"
	"compress/gzip"
	"errors"
	"time"
)

// ErrUnavailable 表示本二进制没有嵌入 SpiderMonkey（CGO_ENABLED=0 或带 nolibwebp/nomozjs 标签的构建）
var ErrUnavailable = errors.New("mozjs: 本构建未嵌入 SpiderMonkey（需 CGO_ENABLED=1 且不带 nolibwebp/nomozjs 标签）")

// Available 报告本构建是否嵌入了 SpiderMonkey
const Available = false

// Handle 是上下文根表中一个 JS 值的句柄，0 表示 undefined
type Handle uint32

// Kind 是 Value 的种类
type Kind int32

// Kind 的取值与 mozjs.h 的 MZ_* 一致
const (
	KindUndefined Kind = 0
	KindNull      Kind = 1
	KindBool      Kind = 2
	KindNumber    Kind = 3
	KindString    Kind = 4
	KindHandle    Kind = 5
)

// Value 是跨边界的 JS 值：原始值按种类保存，其余值为句柄
type Value struct {
	Kind   Kind
	Bool   bool
	Number float64
	String string
	Handle Handle
}

// Undefined 返回 undefined
func Undefined() Value { return Value{} }

// Null 返回 null
func Null() Value { return Value{Kind: KindNull} }

// Bool 返回布尔值
func Bool(value bool) Value { return Value{Kind: KindBool, Bool: value} }

// Number 返回数值
func Number(value float64) Value { return Value{Kind: KindNumber, Number: value} }

// String 返回字符串
func String(value string) Value { return Value{Kind: KindString, String: value} }

// Object 返回句柄引用的值
func Object(handle Handle) Value { return Value{Kind: KindHandle, Handle: handle} }

// NativeFunc 是由 JS 调用的 Go 函数，返回错误时在 JS 中抛出
type NativeFunc func(call *Call) error

// ThrowError 让原生函数以当前全局对象中名为 Type 的错误构造器抛出 Message
type ThrowError struct {
	Type    string
	Message string
}

func (err *ThrowError) Error() string { return err.Type + ": " + err.Message }

// Error 是 JS 抛出的异常，Value 为异常值的句柄
type Error struct {
	Message  string
	Stack    string
	Filename string
	Line     int
	Column   int
	Value    Handle
}

func (err *Error) Error() string {
	if err.Stack != "" {
		return err.Message + "\n" + err.Stack
	}
	return err.Message
}

// Options 是新建上下文的参数
type Options struct {
	HeapBytes uint32
	Locale    string
}

// Context 在无 cgo 构建中无法创建
type Context struct{}

// Call 在无 cgo 构建中不会出现
type Call struct{}

var start = time.Now()

// Init 在无 cgo 构建中总是失败
func Init() error { return ErrUnavailable }

// NewContext 在无 cgo 构建中总是失败
func NewContext(Options) (*Context, error) { return nil, ErrUnavailable }

// SetCamouLocale 在无 cgo 构建中不做任何事
func SetCamouLocale(string) {}

// Now 返回单调时钟的毫秒数
func Now() float64 { return float64(time.Since(start).Microseconds()) / 1000 }

// SetClock 在无 cgo 构建中不做任何事
func SetClock(base, pace, limit float64) {}

// CPU 在无 cgo 构建中返回 0
func CPU() float64 { return 0 }

// Clock 在无 cgo 构建中返回单调时钟
func Clock() float64 { return Now() }

// Gzip 用标准库压缩（与 cgo 版的 zlib-rs 输出不保证逐字节一致），头部操作系统字节同样写 10
func Gzip(data []byte) []byte {
	var out bytes.Buffer
	writer := gzip.NewWriter(&out)
	writer.OS = 10
	_, _ = writer.Write(data)
	_ = writer.Close()
	return out.Bytes()
}

func (cx *Context) Close()                            {}
func (cx *Context) Release(Handle)                    {}
func (cx *Context) ReleaseValue(Value)                {}
func (cx *Context) RunJobs()                          {}
func (cx *Context) ClearKeptObjects()                 {}
func (cx *Context) SetMicrotaskReporter(Handle)       {}
func (cx *Context) SetTrustedTypes(Handle, bool)      {}
func (cx *Context) SetThrowLocation(string, int, int) {}
func (cx *Context) Interrupt()                        {}
func (cx *Context) GC()                               {}
func (cx *Context) ShrinkGC()                         {}

func (cx *Context) NewGlobal(Handle, string) (Handle, error) { return 0, ErrUnavailable }
func (cx *Context) Evaluate(Handle, string, string, int) (Value, error) {
	return Value{}, ErrUnavailable
}
func (cx *Context) Call(Handle, Handle, ...Value) (Value, error) { return Value{}, ErrUnavailable }
func (cx *Context) Get(Handle, string) (Value, error)            { return Value{}, ErrUnavailable }
func (cx *Context) Set(Handle, string, Value) error              { return ErrUnavailable }
func (cx *Context) NewObject(Handle) (Handle, error)             { return 0, ErrUnavailable }
func (cx *Context) ParseJSON(Handle, string) (Value, error)      { return Value{}, ErrUnavailable }
func (cx *Context) NewFunction(Handle, string, int, NativeFunc) (Handle, error) {
	return 0, ErrUnavailable
}
func (cx *Context) InstallHostNatives(Handle) error   { return ErrUnavailable }
func (cx *Context) InstallWindowNatives(Handle) error { return ErrUnavailable }
func (cx *Context) FlushRejections(Handle) error      { return ErrUnavailable }

func (call *Call) Arg(int) Value               { return Value{} }
func (call *Call) ArgString(int) string        { return "" }
func (call *Call) ArgNumber(int) float64       { return 0 }
func (call *Call) ArgBytes(int) ([]byte, bool) { return nil, false }
func (call *Call) Return(Value)                {}
func (call *Call) ReturnBytes([]byte)          {}
