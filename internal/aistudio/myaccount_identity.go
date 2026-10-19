package aistudio

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const myAccountIdentityURL = "https://myaccount.google.com/u/0/"
const myAccountIdentityUA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

var myAccountAssignment = regexp.MustCompile(`(?s)^\s*(?:window\.)?WIZ_global_data\s*=\s*(\{.*\})\s*;?\s*$`)
var myAccountEmail = regexp.MustCompile("^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,200}@[A-Za-z0-9.-]{1,100}$")
var myAccountGaia = regexp.MustCompile(`^\d{10,32}$`)

func parseMyAccountIdentity(text string) (*SelectedIdentity, error) {
	root, err := html.Parse(strings.NewReader(text))
	if err != nil {
		return nil, err
	}
	var bases, scripts []string
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode && n.Data == "base" {
			for _, a := range n.Attr {
				if a.Key == "href" {
					bases = append(bases, a.Val)
				}
			}
		}
		if n.Type == html.ElementNode && n.Data == "script" {
			var b strings.Builder
			for c := n.FirstChild; c != nil; c = c.NextSibling {
				if c.Type == html.TextNode {
					b.WriteString(c.Data)
				}
			}
			if strings.HasPrefix(strings.TrimSpace(b.String()), "window.WIZ_global_data") || strings.HasPrefix(strings.TrimSpace(b.String()), "WIZ_global_data") {
				scripts = append(scripts, b.String())
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(root)
	fail := fmt.Errorf("Google 默认账号页无法严格核对身份，请重新导入")
	if len(bases) != 1 || bases[0] != myAccountIdentityURL || len(scripts) != 1 {
		return nil, fail
	}
	m := myAccountAssignment.FindStringSubmatch(scripts[0])
	if len(m) != 2 {
		return nil, fail
	}
	// Reject duplicate root keys rather than accepting last-value-wins.
	dec := json.NewDecoder(strings.NewReader(m[1]))
	tok, err := dec.Token()
	if err != nil || tok != json.Delim('{') {
		return nil, fail
	}
	d := map[string]json.RawMessage{}
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			return nil, fail
		}
		k, ok := key.(string)
		if !ok {
			return nil, fail
		}
		if _, exists := d[k]; exists {
			return nil, fail
		}
		var v json.RawMessage
		if dec.Decode(&v) != nil {
			return nil, fail
		}
		d[k] = v
	}
	if _, err = dec.Token(); err != nil {
		return nil, fail
	}
	var trailing any
	if dec.Decode(&trailing) != io.EOF {
		return nil, fail
	}
	get := func(k string) string {
		var v string
		if json.Unmarshal(d[k], &v) != nil {
			return ""
		}
		return v
	}
	email, gaia := get("oPEP7c"), get("qDCSke")
	if !myAccountEmail.MatchString(email) || !myAccountGaia.MatchString(gaia) || get("W3Yyqf") != gaia || get("S06Grb") != gaia {
		return nil, fail
	}
	return &SelectedIdentity{Email: strings.ToLower(email), GaiaID: gaia, AuthUser: 0}, nil
}

func resolveMyAccountDefault(ctx context.Context, client *http.Client, state StorageState, id SelectedIdentity) (string, error) {
	// This path is explicitly issued by the portal for the default account only.
	var proof struct {
		Method      string `json:"method"`
		DefaultOnly bool   `json:"default_account_only"`
	}
	if json.Unmarshal(state.extra["xiaoguofan_identity_verification"], &proof) != nil || proof.Method != "myaccount-default-v1" || !proof.DefaultOnly || id.AuthUser != 0 {
		return "", fmt.Errorf("Google 账号列表为空，请重新核对身份")
	}
	cookie, err := state.CookieHeader(myAccountIdentityURL, time.Now())
	if err != nil {
		return "", err
	}
	if cookie == "" {
		return "", fmt.Errorf("缺少 Google 默认账号会话")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, myAccountIdentityURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Cookie", cookie)
	req.Header.Set("User-Agent", myAccountIdentityUA)
	isolated := *client
	isolated.Jar = nil
	isolated.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	res, err := isolated.Do(req)
	if err != nil {
		return "", fmt.Errorf("无法核对 Google 默认账号")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 || !strings.Contains(strings.ToLower(res.Header.Get("Content-Type")), "text/html") {
		return "", fmt.Errorf("Google 默认账号会话未通过核对")
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, 2000001))
	if err != nil || len(raw) > 2000000 {
		return "", fmt.Errorf("Google 默认账号响应无效")
	}
	found, err := parseMyAccountIdentity(string(raw))
	if err != nil {
		return "", err
	}
	if found.GaiaID != id.GaiaID || !strings.EqualFold(found.Email, id.Email) {
		return "", fmt.Errorf("Google 默认账号已变化，请重新核对")
	}
	return "0", nil
}
