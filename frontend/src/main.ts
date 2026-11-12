import { mount } from "svelte";
import App from "./App.svelte";
import "./style.css";
import { applyTheme, readTheme } from './theme';

applyTheme(readTheme(), false);
mount(App, { target: document.getElementById("app")! });
