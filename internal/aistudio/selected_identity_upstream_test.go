package aistudio

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestSelectedIdentityFallbackPreservesUpstreamSession(t *testing.T) {
	for _, n := range []int{0, 1, 9} {
		got, err := ResolveSelectedAuthUser(context.Background(), nil, StorageState{}, n)
		if err != nil || got != string(rune('0'+n)) {
			t.Fatalf("fallback %d: got %q, err %v", n, got, err)
		}
	}
	for _, n := range []int{-1, 100} {
		if _, err := ResolveSelectedAuthUser(context.Background(), nil, StorageState{}, n); err == nil {
			t.Fatalf("invalid fallback %d accepted", n)
		}
	}
}

func TestSelectedIdentityMetadataWinsOverUpstreamSession(t *testing.T) {
	var state StorageState
	if err := json.Unmarshal([]byte(`{"cookies":[],"origins":[],"xiaoguofan_identity":{"email":"pro@example.com","gaia_id":"222","authuser":4}}`), &state); err != nil {
		t.Fatal(err)
	}
	email, id := "pro@example.com", "222"
	row := append([]byte{26, byte(len(email))}, []byte(email)...)
	row = append(row, 72, 1, 82, byte(len(id)))
	row = append(row, []byte(id)...)
	payload := base64.StdEncoding.EncodeToString(append([]byte{10, byte(len(row))}, row...))
	client := &http.Client{Transport: identityRoundTripper(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(payload)), Header: http.Header{}}, nil
	})}
	got, err := ResolveSelectedAuthUser(context.Background(), client, state, 7)
	if err != nil || got != "0" {
		t.Fatalf("metadata must win: got %q, err %v", got, err)
	}
	payload = "invalid"
	if _, err := ResolveSelectedAuthUser(context.Background(), client, state, 7); err == nil {
		t.Fatal("identity failure must not fall back")
	}
}
