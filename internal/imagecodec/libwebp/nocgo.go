//go:build !cgo || nolibwebp

// 无 cgo 构建（CI 单元测试、go vet、交叉编译检查）用的桩；生产构建（CGO_ENABLED=1、不带 nomozjs/nolibwebp）不编译本文件。
package libwebp

import (
	"errors"
	"image"
	"io"
)

// ErrUnavailable 表示本二进制没有链接 libwebp（CGO_ENABLED=0 或带 nolibwebp/nomozjs 标签的构建）
var ErrUnavailable = errors.New("libwebp: 本构建未链接 libwebp（需 CGO_ENABLED=1 且不带 nolibwebp/nomozjs 标签）")

// Available 报告本构建是否链接了 libwebp
const Available = false

// Encode 在无 cgo 构建中总是返回 ErrUnavailable
func Encode(io.Writer, image.Image, int) error { return ErrUnavailable }
