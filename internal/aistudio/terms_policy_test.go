package aistudio

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type termsPolicyTransport struct {
	calls []string
	body  string
}

func (t *termsPolicyTransport) Do(_ context.Context, r RPCRequest) (*RPCResponse, error) {
	t.calls = append(t.calls, r.Method)
	return &RPCResponse{StatusCode: 200, Header: http.Header{"Content-Type": []string{JSONProtobufContentType}}, Body: io.NopCloser(strings.NewReader(t.body))}, nil
}
func TestTermsPolicyNeverAcceptsForUser(t *testing.T) {
	tr := &termsPolicyTransport{body: `[null,null,null,null,null,false,123]`}
	c := &Client{transport: tr, termsAccepted: make(map[string]struct{})}
	err := c.EnsureTermsAccepted(context.Background(), "fixture")
	if err == nil || !strings.Contains(err.Error(), "官网") {
		t.Fatal("must require user's own acceptance", err)
	}
	if len(tr.calls) != 1 || tr.calls[0] != "GetLoggingContext" {
		t.Fatal("mutating terms RPC sent", tr.calls)
	}
	tr.body = `[null,null,null,null,null,true,123]`
	if err = c.EnsureTermsAccepted(context.Background(), "fixture"); err != nil {
		t.Fatal(err)
	}
	if len(tr.calls) != 2 {
		t.Fatal("declined state incorrectly cached")
	}
	if err = c.EnsureTermsAccepted(context.Background(), "fixture"); err != nil || len(tr.calls) != 2 {
		t.Fatal("accepted cache failed", err)
	}
}
