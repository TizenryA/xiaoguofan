package aistudio

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type termsPolicyTransport struct {
	calls []RPCRequest
}

func (t *termsPolicyTransport) Do(_ context.Context, r RPCRequest) (*RPCResponse, error) {
	t.calls = append(t.calls, r)
	return &RPCResponse{StatusCode: 200, Header: http.Header{"Content-Type": []string{JSONProtobufContentType}}, Body: io.NopCloser(strings.NewReader("[]"))}, nil
}

func TestTermsPolicyUsesPageLoggingContext(t *testing.T) {
	tr := &termsPolicyTransport{}
	c := &Client{transport: tr, termsAccepted: make(map[string]struct{})}
	logging := []byte(`[null,null,null,null,null,false,123]`)
	if err := c.acceptTerms(context.Background(), "fixture", logging); err != nil {
		t.Fatal(err)
	}
	if len(tr.calls) != 1 || tr.calls[0].Method != "AcceptTerms" || tr.calls[0].AccountID != "fixture" {
		t.Fatalf("expected only page-driven AcceptTerms, got %+v", tr.calls)
	}
	if got := string(tr.calls[0].Body); got != `["users/me",null,123,null,null,1,null,null,5]` {
		t.Fatalf("unexpected onboarding body: %s", got)
	}
	if err := c.acceptTerms(context.Background(), "fixture", logging); err != nil || len(tr.calls) != 1 {
		t.Fatal("accepted account should reuse terms state", err)
	}
	if err := c.acceptTerms(context.Background(), "already-accepted", []byte(`[null,null,null,null,null,true,123]`)); err != nil || len(tr.calls) != 1 {
		t.Fatal("already accepted page should not send AcceptTerms", err)
	}
}
