package aistudio

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

const accountPageFixture = `<html><head><base href="https://myaccount.google.com/u/0/"><script nonce="fixture">window.WIZ_global_data = {"oPEP7c":"fixture@example.com","qDCSke":"123456789012345678901","W3Yyqf":"123456789012345678901","S06Grb":"123456789012345678901"};</script></head></html>`

func TestDefaultAccountStrictIdentityParsing(t *testing.T) {
	id, err := parseMyAccountIdentity(accountPageFixture)
	if err != nil || id.Email != "fixture@example.com" || id.AuthUser != 0 {
		t.Fatal("fixture identity rejected", err)
	}
	for _, bad := range []string{"fixture@example.com 123456789012345678901", strings.Replace(accountPageFixture, `"W3Yyqf":"123456789012345678901"`, `"W3Yyqf":"999999999999999999999"`, 1), accountPageFixture + accountPageFixture, strings.Replace(accountPageFixture, "https://myaccount.google.com/u/0/", "https://evil.example/", 1), strings.Replace(accountPageFixture, `"oPEP7c":"fixture@example.com"`, `"oPEP7c":"wrong@example.com","oPEP7c":"fixture@example.com"`, 1)} {
		if _, err := parseMyAccountIdentity(bad); err == nil {
			t.Fatal("unsafe account page accepted")
		}
	}
}
func TestDefaultAccountEmptyListFallbackAndWorkerReverification(t *testing.T) {
	var state StorageState
	json.Unmarshal([]byte(`{"cookies":[{"name":"SID","value":"ISOLATED-SID","domain":".google.com","path":"/","expires":-1},{"name":"OSID","value":"ISOLATED-OSID","domain":"myaccount.google.com","path":"/","expires":-1}],"origins":[],"xiaoguofan_identity":{"email":"fixture@example.com","gaia_id":"123456789012345678901","authuser":0},"xiaoguofan_identity_verification":{"method":"myaccount-default-v1","default_account_only":true}}`), &state)
	page := accountPageFixture
	status := 200
	calls := 0
	client := &http.Client{Transport: identityRoundTripper(func(r *http.Request) (*http.Response, error) {
		body := ""
		headers := http.Header{}
		code := 200
		if r.URL.Host == "myaccount.google.com" {
			calls++
			if r.URL.String() != myAccountIdentityURL || !strings.Contains(r.Header.Get("Cookie"), "OSID=ISOLATED-OSID") {
				t.Fatal("wrong fixed target or missing cookie")
			}
			body = page
			code = status
			headers.Set("Content-Type", "text/html")
		}
		return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(body)), Header: headers}, nil
	})}
	got, err := ResolveSelectedAuthUser(context.Background(), client, state)
	if err != nil || got != "0" || calls != 1 {
		t.Fatal("default identity fallback not used", got, err, calls)
	}
	page = strings.Replace(accountPageFixture, "fixture@example.com", "wrong@example.com", 1)
	if _, err = ResolveSelectedAuthUser(context.Background(), client, state); err == nil {
		t.Fatal("changed identity accepted")
	}
	page = accountPageFixture
	status = 302
	if _, err = ResolveSelectedAuthUser(context.Background(), client, state); err == nil {
		t.Fatal("redirect accepted")
	}
	status = 200
	delete(state.extra, "xiaoguofan_identity_verification")
	calls = 0
	if _, err = ResolveSelectedAuthUser(context.Background(), client, state); err == nil || calls != 0 {
		t.Fatal("unmarked account accepted fallback")
	}
}
